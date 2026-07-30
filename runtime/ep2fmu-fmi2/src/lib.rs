//! Generic FMI 2.0 Co-Simulation bridge for an isolated ep2fmu worker.
#![allow(clippy::missing_safety_doc)]

use std::collections::HashSet;
use std::ffi::{c_char, c_void, CStr, CString};
use std::io::{BufReader, BufWriter};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command as ProcessCommand, Stdio};
use std::ptr;

use ep2fmu_protocol::{read_message, write_message, Command, LogLevel, Response};
use serde::Deserialize;
use url::Url;

type FmiComponent = *mut c_void;
type FmiStatus = i32;
type FmiBoolean = i32;
type FmiValueReference = u32;
type FmiReal = f64;
type FmiInteger = i32;
type FmiString = *const c_char;

const FMI_OK: FmiStatus = 0;
const FMI_WARNING: FmiStatus = 1;
const FMI_DISCARD: FmiStatus = 2;
const FMI_ERROR: FmiStatus = 3;
const FMI_FALSE: FmiBoolean = 0;
const FMI_CO_SIMULATION: i32 = 1;

const VR_ENERGYPLUS_HOME: FmiValueReference = 1;
const VR_OUTPUT_DIRECTORY: FmiValueReference = 2;
const VR_KEEP_OUTPUTS: FmiValueReference = 3;
const VR_RUN_READ_VARS: FmiValueReference = 4;
const INPUT_VR_BASE: FmiValueReference = 1000;
const OUTPUT_VR_BASE: FmiValueReference = 100000;

type Logger =
    unsafe extern "C" fn(*mut c_void, *const c_char, FmiStatus, *const c_char, *const c_char, ...);

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Fmi2CallbackFunctions {
    logger: Option<Logger>,
    allocate_memory: Option<unsafe extern "C" fn(usize, usize) -> *mut c_void>,
    free_memory: Option<unsafe extern "C" fn(*mut c_void)>,
    step_finished: Option<unsafe extern "C" fn(*mut c_void, FmiStatus)>,
    component_environment: *mut c_void,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Lifecycle {
    Instantiated,
    Initialization,
    Running,
    Terminated,
    Error,
}

#[derive(Deserialize)]
struct RuntimeMetadata {
    zone_step_seconds: f64,
    stop_time_seconds: f64,
    inputs: Vec<RuntimeInput>,
    outputs: Vec<serde_json::Value>,
}

#[derive(Deserialize)]
struct RuntimeInput {
    start: f64,
}

struct WorkerProcess {
    child: Child,
    writer: BufWriter<ChildStdin>,
    reader: BufReader<ChildStdout>,
}

struct Instance {
    name: CString,
    _guid: CString,
    resource_dir: PathBuf,
    callbacks: Fmi2CallbackFunctions,
    logging_on: bool,
    log_categories: Option<HashSet<String>>,
    state: Lifecycle,
    energyplus_home: CString,
    output_directory: CString,
    keep_outputs: bool,
    run_read_vars: bool,
    inputs: Vec<f64>,
    outputs: Vec<f64>,
    zone_step: f64,
    model_stop_time: f64,
    experiment_start: f64,
    experiment_stop: Option<f64>,
    current_time: f64,
    worker: Option<WorkerProcess>,
}

impl Instance {
    fn from_component<'a>(component: FmiComponent) -> Result<&'a mut Self, FmiStatus> {
        if component.is_null() {
            return Err(FMI_ERROR);
        }
        Ok(unsafe { &mut *(component as *mut Self) })
    }

    fn log(&self, status: FmiStatus, category: &str, message: &str) {
        if !self.logging_on && status < FMI_WARNING {
            return;
        }
        if status < FMI_WARNING
            && self
                .log_categories
                .as_ref()
                .is_some_and(|categories| !categories.contains(category))
        {
            return;
        }
        let Some(logger) = self.callbacks.logger else {
            return;
        };
        let category = CString::new(category).unwrap_or_else(|_| CString::new("log").unwrap());
        let message =
            CString::new(message).unwrap_or_else(|_| CString::new("invalid log message").unwrap());
        let format = c"%s";
        unsafe {
            logger(
                self.callbacks.component_environment,
                self.name.as_ptr(),
                status,
                category.as_ptr(),
                format.as_ptr(),
                message.as_ptr(),
            );
        }
    }

    fn fail(&mut self, message: impl AsRef<str>) -> FmiStatus {
        self.log(FMI_ERROR, "error", message.as_ref());
        self.state = Lifecycle::Error;
        FMI_ERROR
    }

    fn send(&mut self, command: &Command) -> Result<(), String> {
        let worker = self
            .worker
            .as_mut()
            .ok_or_else(|| "worker is not running".to_owned())?;
        write_message(&mut worker.writer, command).map_err(|error| error.to_string())
    }

    fn receive(&mut self) -> Result<Response, String> {
        let response = {
            let worker = self
                .worker
                .as_mut()
                .ok_or_else(|| "worker is not running".to_owned())?;
            read_message(&mut worker.reader).map_err(|error| error.to_string())?
        };
        Ok(response)
    }

    fn receive_non_log(&mut self) -> Result<Response, String> {
        loop {
            match self.receive()? {
                Response::Log {
                    level,
                    category,
                    message,
                } => {
                    let status = match level {
                        LogLevel::Debug | LogLevel::Info => FMI_OK,
                        LogLevel::Warning => FMI_WARNING,
                        LogLevel::Error => FMI_ERROR,
                    };
                    self.log(status, &category, &message);
                }
                response => return Ok(response),
            }
        }
    }

    fn spawn_worker(&mut self) -> Result<(), String> {
        let worker_path = self
            .resource_dir
            .join("bin")
            .join(platform_directory())
            .join(worker_filename());
        if !worker_path.is_file() {
            return Err(format!("FMU worker not found: {}", worker_path.display()));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            let mut permissions = std::fs::metadata(&worker_path)
                .map_err(|error| {
                    format!(
                        "cannot inspect worker permissions {}: {error}",
                        worker_path.display()
                    )
                })?
                .permissions();
            if permissions.mode() & 0o111 == 0 {
                permissions.set_mode(permissions.mode() | 0o700);
                std::fs::set_permissions(&worker_path, permissions).map_err(|error| {
                    format!(
                        "cannot make FMU worker executable {}: {error}",
                        worker_path.display()
                    )
                })?;
            }
        }
        let mut child = ProcessCommand::new(&worker_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|error| format!("cannot start {}: {error}", worker_path.display()))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "worker stdin is unavailable".to_owned())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "worker stdout is unavailable".to_owned())?;
        self.worker = Some(WorkerProcess {
            child,
            writer: BufWriter::new(stdin),
            reader: BufReader::new(stdout),
        });
        let home = self.energyplus_home.to_string_lossy().into_owned();
        let home = if home.is_empty() {
            std::env::var("ENERGYPLUS_HOME").unwrap_or_default()
        } else {
            home
        };
        self.send(&Command::Initialize {
            resource_dir: self.resource_dir.to_string_lossy().into_owned(),
            energyplus_home: home,
            output_dir: self.output_directory.to_string_lossy().into_owned(),
            keep_outputs: self.keep_outputs,
            run_read_vars: self.run_read_vars,
            instance_name: self.name.to_string_lossy().into_owned(),
        })?;
        match self.receive_non_log()? {
            Response::Ready {
                outputs,
                zone_step_seconds,
                stop_time_seconds,
            } => {
                if outputs.len() != self.outputs.len() {
                    return Err(format!(
                        "worker returned {} outputs, expected {}",
                        outputs.len(),
                        self.outputs.len()
                    ));
                }
                self.outputs = outputs;
                self.zone_step = zone_step_seconds;
                self.model_stop_time = stop_time_seconds;
                Ok(())
            }
            Response::Error { message } => Err(message),
            response => Err(format!("unexpected initialization response: {response:?}")),
        }
    }

    fn stop_worker(&mut self) {
        if self.worker.is_none() {
            return;
        }
        let _ = self.send(&Command::Shutdown);
        loop {
            match self.receive_non_log() {
                Ok(Response::Terminated) | Ok(Response::Error { .. }) | Err(_) => break,
                Ok(_) => {}
            }
        }
        if let Some(mut worker) = self.worker.take() {
            let _ = worker.child.wait();
        }
    }

    fn reset_values(&mut self, metadata: &RuntimeMetadata) {
        self.inputs = metadata.inputs.iter().map(|input| input.start).collect();
        self.outputs = vec![0.0; metadata.outputs.len()];
        self.zone_step = metadata.zone_step_seconds;
        self.model_stop_time = metadata.stop_time_seconds;
        self.current_time = 0.0;
        self.experiment_start = 0.0;
        self.experiment_stop = None;
    }
}

impl Drop for Instance {
    fn drop(&mut self) {
        self.stop_worker();
    }
}

fn platform_directory() -> &'static str {
    #[cfg(all(target_os = "linux", target_pointer_width = "64"))]
    {
        "linux64"
    }
    #[cfg(all(target_os = "windows", target_pointer_width = "64"))]
    {
        "win64"
    }
    #[cfg(target_os = "macos")]
    {
        // FMI 2 defines one tuple for 64-bit macOS. Release artifacts are
        // universal2 and contain both x86_64 and arm64 slices.
        "darwin64"
    }
}

fn worker_filename() -> &'static str {
    if cfg!(target_os = "windows") {
        "ep2fmu-worker.exe"
    } else {
        "ep2fmu-worker"
    }
}

fn required_cstr(value: *const c_char, label: &str) -> Result<CString, String> {
    if value.is_null() {
        return Err(format!("{label} must not be null"));
    }
    let bytes = unsafe { CStr::from_ptr(value) }.to_bytes();
    CString::new(bytes).map_err(|_| format!("{label} contains a null byte"))
}

fn resource_path(value: *const c_char) -> Result<PathBuf, String> {
    let value = required_cstr(value, "resourceLocation")?;
    let text = value.to_string_lossy();
    let url = Url::parse(&text).map_err(|error| format!("invalid resourceLocation: {error}"))?;
    if url.scheme() != "file" {
        return Err("resourceLocation must use the file scheme".to_owned());
    }
    url.to_file_path()
        .map_err(|_| "resourceLocation cannot be converted to a local path".to_owned())
}

fn load_metadata(resource_dir: &Path) -> Result<RuntimeMetadata, String> {
    let path = resource_dir.join("ep2fmu-config.json");
    let bytes =
        std::fs::read(&path).map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("invalid {}: {error}", path.display()))
}

#[no_mangle]
pub extern "C" fn fmi2GetTypesPlatform() -> FmiString {
    static PLATFORM: &[u8] = b"default\0";
    PLATFORM.as_ptr() as FmiString
}

#[no_mangle]
pub extern "C" fn fmi2GetVersion() -> FmiString {
    static VERSION: &[u8] = b"2.0\0";
    VERSION.as_ptr() as FmiString
}

#[no_mangle]
pub unsafe extern "C" fn fmi2Instantiate(
    instance_name: FmiString,
    fmu_type: i32,
    guid: FmiString,
    resource_location: FmiString,
    functions: *const Fmi2CallbackFunctions,
    _visible: FmiBoolean,
    logging_on: FmiBoolean,
) -> FmiComponent {
    if fmu_type != FMI_CO_SIMULATION || functions.is_null() {
        return ptr::null_mut();
    }
    let result = (|| -> Result<Instance, String> {
        let resource_dir = resource_path(resource_location)?;
        let metadata = load_metadata(&resource_dir)?;
        let callbacks = *functions;
        let mut instance = Instance {
            name: required_cstr(instance_name, "instanceName")?,
            _guid: required_cstr(guid, "guid")?,
            resource_dir,
            callbacks,
            logging_on: logging_on != FMI_FALSE,
            log_categories: None,
            state: Lifecycle::Instantiated,
            energyplus_home: CString::new("").unwrap(),
            output_directory: CString::new("").unwrap(),
            keep_outputs: false,
            run_read_vars: false,
            inputs: Vec::new(),
            outputs: Vec::new(),
            zone_step: metadata.zone_step_seconds,
            model_stop_time: metadata.stop_time_seconds,
            experiment_start: 0.0,
            experiment_stop: None,
            current_time: 0.0,
            worker: None,
        };
        instance.reset_values(&metadata);
        Ok(instance)
    })();
    match result {
        Ok(instance) => Box::into_raw(Box::new(instance)) as FmiComponent,
        Err(_) => ptr::null_mut(),
    }
}

#[no_mangle]
pub unsafe extern "C" fn fmi2FreeInstance(component: FmiComponent) {
    if !component.is_null() {
        drop(Box::from_raw(component as *mut Instance));
    }
}

#[no_mangle]
pub unsafe extern "C" fn fmi2SetDebugLogging(
    component: FmiComponent,
    logging_on: FmiBoolean,
    categories: usize,
    category: *const FmiString,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    instance.logging_on = logging_on != FMI_FALSE;
    if categories == 0 {
        instance.log_categories = None;
        return FMI_OK;
    }
    if category.is_null() {
        return instance.fail("log category array is null");
    }
    let mut selected = HashSet::with_capacity(categories);
    for pointer in std::slice::from_raw_parts(category, categories) {
        if pointer.is_null() {
            return instance.fail("log category is null");
        }
        selected.insert(CStr::from_ptr(*pointer).to_string_lossy().into_owned());
    }
    instance.log_categories = Some(selected);
    FMI_OK
}

#[no_mangle]
pub extern "C" fn fmi2SetupExperiment(
    component: FmiComponent,
    _tolerance_defined: FmiBoolean,
    _tolerance: FmiReal,
    start_time: FmiReal,
    stop_time_defined: FmiBoolean,
    stop_time: FmiReal,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if instance.state != Lifecycle::Instantiated || start_time != 0.0 {
        return instance.fail("only a zero FMI start time is supported");
    }
    instance.experiment_start = start_time;
    instance.current_time = start_time;
    instance.experiment_stop = (stop_time_defined != FMI_FALSE).then_some(stop_time);
    if stop_time_defined != FMI_FALSE && stop_time > instance.model_stop_time {
        return instance.fail("FMI stop time exceeds the EnergyPlus RunPeriod");
    }
    FMI_OK
}

#[no_mangle]
pub extern "C" fn fmi2EnterInitializationMode(component: FmiComponent) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if instance.state != Lifecycle::Instantiated {
        return instance.fail("invalid lifecycle transition to initialization mode");
    }
    instance.state = Lifecycle::Initialization;
    FMI_OK
}

#[no_mangle]
pub extern "C" fn fmi2ExitInitializationMode(component: FmiComponent) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if instance.state != Lifecycle::Initialization {
        return instance.fail("invalid lifecycle transition from initialization mode");
    }
    match instance.spawn_worker() {
        Ok(()) => {
            instance.state = Lifecycle::Running;
            FMI_OK
        }
        Err(message) => instance.fail(message),
    }
}

#[no_mangle]
pub extern "C" fn fmi2Terminate(component: FmiComponent) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    instance.stop_worker();
    instance.state = Lifecycle::Terminated;
    FMI_OK
}

#[no_mangle]
pub extern "C" fn fmi2Reset(component: FmiComponent) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    instance.stop_worker();
    match load_metadata(&instance.resource_dir) {
        Ok(metadata) => {
            instance.reset_values(&metadata);
            instance.state = Lifecycle::Instantiated;
            FMI_OK
        }
        Err(message) => instance.fail(message),
    }
}

#[no_mangle]
pub unsafe extern "C" fn fmi2SetReal(
    component: FmiComponent,
    references: *const FmiValueReference,
    count: usize,
    values: *const FmiReal,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if references.is_null() || values.is_null() {
        return instance.fail("null array passed to fmi2SetReal");
    }
    for index in 0..count {
        let reference = *references.add(index);
        let input = reference
            .checked_sub(INPUT_VR_BASE)
            .map(|value| value as usize);
        let Some(input) = input.filter(|value| *value < instance.inputs.len()) else {
            return instance.fail(format!("unknown Real value reference {reference}"));
        };
        instance.inputs[input] = *values.add(index);
    }
    FMI_OK
}

#[no_mangle]
pub unsafe extern "C" fn fmi2GetReal(
    component: FmiComponent,
    references: *const FmiValueReference,
    count: usize,
    values: *mut FmiReal,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if references.is_null() || values.is_null() {
        return instance.fail("null array passed to fmi2GetReal");
    }
    for index in 0..count {
        let reference = *references.add(index);
        let output = reference
            .checked_sub(OUTPUT_VR_BASE)
            .map(|value| value as usize);
        let Some(output) = output.filter(|value| *value < instance.outputs.len()) else {
            return instance.fail(format!("unknown Real value reference {reference}"));
        };
        *values.add(index) = instance.outputs[output];
    }
    FMI_OK
}

#[no_mangle]
pub unsafe extern "C" fn fmi2SetBoolean(
    component: FmiComponent,
    references: *const FmiValueReference,
    count: usize,
    values: *const FmiBoolean,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if references.is_null() || values.is_null() {
        return instance.fail("null array passed to fmi2SetBoolean");
    }
    for index in 0..count {
        match *references.add(index) {
            VR_KEEP_OUTPUTS => instance.keep_outputs = *values.add(index) != FMI_FALSE,
            VR_RUN_READ_VARS => instance.run_read_vars = *values.add(index) != FMI_FALSE,
            reference => return instance.fail(format!("unknown Boolean reference {reference}")),
        }
    }
    FMI_OK
}

#[no_mangle]
pub unsafe extern "C" fn fmi2GetBoolean(
    component: FmiComponent,
    references: *const FmiValueReference,
    count: usize,
    values: *mut FmiBoolean,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if references.is_null() || values.is_null() {
        return instance.fail("null array passed to fmi2GetBoolean");
    }
    for index in 0..count {
        *values.add(index) = match *references.add(index) {
            VR_KEEP_OUTPUTS => instance.keep_outputs as FmiBoolean,
            VR_RUN_READ_VARS => instance.run_read_vars as FmiBoolean,
            reference => return instance.fail(format!("unknown Boolean reference {reference}")),
        };
    }
    FMI_OK
}

#[no_mangle]
pub unsafe extern "C" fn fmi2SetString(
    component: FmiComponent,
    references: *const FmiValueReference,
    count: usize,
    values: *const FmiString,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if references.is_null() || values.is_null() {
        return instance.fail("null array passed to fmi2SetString");
    }
    for index in 0..count {
        let value = match required_cstr(*values.add(index), "String value") {
            Ok(value) => value,
            Err(message) => return instance.fail(message),
        };
        match *references.add(index) {
            VR_ENERGYPLUS_HOME => instance.energyplus_home = value,
            VR_OUTPUT_DIRECTORY => instance.output_directory = value,
            reference => return instance.fail(format!("unknown String reference {reference}")),
        }
    }
    FMI_OK
}

#[no_mangle]
pub unsafe extern "C" fn fmi2GetString(
    component: FmiComponent,
    references: *const FmiValueReference,
    count: usize,
    values: *mut FmiString,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if references.is_null() || values.is_null() {
        return instance.fail("null array passed to fmi2GetString");
    }
    for index in 0..count {
        *values.add(index) = match *references.add(index) {
            VR_ENERGYPLUS_HOME => instance.energyplus_home.as_ptr(),
            VR_OUTPUT_DIRECTORY => instance.output_directory.as_ptr(),
            reference => return instance.fail(format!("unknown String reference {reference}")),
        };
    }
    FMI_OK
}

#[no_mangle]
pub unsafe extern "C" fn fmi2GetInteger(
    component: FmiComponent,
    _references: *const FmiValueReference,
    count: usize,
    _values: *mut FmiInteger,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if count == 0 {
        FMI_OK
    } else {
        instance.fail("this FMU exposes no Integer variables")
    }
}

#[no_mangle]
pub unsafe extern "C" fn fmi2SetInteger(
    component: FmiComponent,
    _references: *const FmiValueReference,
    count: usize,
    _values: *const FmiInteger,
) -> FmiStatus {
    fmi2GetInteger(component, ptr::null(), count, ptr::null_mut())
}

#[no_mangle]
pub extern "C" fn fmi2DoStep(
    component: FmiComponent,
    current_communication_point: FmiReal,
    communication_step_size: FmiReal,
    _no_set_state_prior: FmiBoolean,
) -> FmiStatus {
    let Ok(instance) = Instance::from_component(component) else {
        return FMI_ERROR;
    };
    if instance.state != Lifecycle::Running {
        return instance.fail("fmi2DoStep requires a running instance");
    }
    let tolerance = 1e-9_f64.max(communication_step_size.abs() * 1e-9);
    if (current_communication_point - instance.current_time).abs() > tolerance {
        return instance.fail("currentCommunicationPoint does not match FMU time");
    }
    let ratio = communication_step_size / instance.zone_step;
    if communication_step_size <= 0.0 || ratio.round() < 1.0 || (ratio - ratio.round()).abs() > 1e-9
    {
        return instance.fail(format!(
            "communicationStepSize must be a positive multiple of {} seconds",
            instance.zone_step
        ));
    }
    let stop = instance.experiment_stop.unwrap_or(instance.model_stop_time);
    if current_communication_point + communication_step_size > stop + tolerance {
        return instance.fail("step exceeds the configured stop time");
    }
    let command = Command::Step {
        current_time: current_communication_point,
        step_size: communication_step_size,
        inputs: instance.inputs.clone(),
    };
    if let Err(message) = instance.send(&command) {
        return instance.fail(message);
    }
    match instance.receive_non_log() {
        Ok(Response::StepComplete { time, outputs }) => {
            instance.current_time = time;
            instance.outputs = outputs;
            FMI_OK
        }
        Ok(Response::Error { message }) => instance.fail(message),
        Ok(Response::Terminated) => {
            instance.state = Lifecycle::Terminated;
            FMI_DISCARD
        }
        Ok(response) => instance.fail(format!("unexpected step response: {response:?}")),
        Err(message) => instance.fail(message),
    }
}

macro_rules! unsupported {
    ($name:ident ($($argument:ident : $ty:ty),*) ) => {
        #[no_mangle]
        pub extern "C" fn $name($($argument: $ty),*) -> FmiStatus {
            $(let _ = $argument;)*
            FMI_ERROR
        }
    };
}

unsupported!(fmi2CancelStep(component: FmiComponent));
unsupported!(fmi2GetStatus(component: FmiComponent, kind: i32, value: *mut FmiStatus));
unsupported!(fmi2GetRealStatus(component: FmiComponent, kind: i32, value: *mut FmiReal));
unsupported!(fmi2GetIntegerStatus(component: FmiComponent, kind: i32, value: *mut FmiInteger));
unsupported!(fmi2GetBooleanStatus(component: FmiComponent, kind: i32, value: *mut FmiBoolean));
unsupported!(fmi2GetStringStatus(component: FmiComponent, kind: i32, value: *mut FmiString));
unsupported!(fmi2SetRealInputDerivatives(component: FmiComponent, vr: *const FmiValueReference, nvr: usize, order: *const FmiInteger, value: *const FmiReal));
unsupported!(fmi2GetRealOutputDerivatives(component: FmiComponent, vr: *const FmiValueReference, nvr: usize, order: *const FmiInteger, value: *mut FmiReal));
unsupported!(fmi2GetFMUstate(component: FmiComponent, state: *mut *mut c_void));
unsupported!(fmi2SetFMUstate(component: FmiComponent, state: *mut c_void));
unsupported!(fmi2FreeFMUstate(component: FmiComponent, state: *mut *mut c_void));
unsupported!(fmi2SerializedFMUstateSize(component: FmiComponent, state: *mut c_void, size: *mut usize));
unsupported!(fmi2SerializeFMUstate(component: FmiComponent, state: *mut c_void, bytes: *mut u8, size: usize));
unsupported!(fmi2DeSerializeFMUstate(component: FmiComponent, bytes: *const u8, size: usize, state: *mut *mut c_void));
unsupported!(fmi2GetDirectionalDerivative(component: FmiComponent, unknown: *const FmiValueReference, n_unknown: usize, known: *const FmiValueReference, n_known: usize, dv_known: *const FmiReal, dv_unknown: *mut FmiReal));

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_file_resource_url() {
        let path = std::env::temp_dir();
        let url = Url::from_directory_path(&path).unwrap();
        let c = CString::new(url.as_str()).unwrap();
        assert_eq!(resource_path(c.as_ptr()).unwrap(), path);
    }
}

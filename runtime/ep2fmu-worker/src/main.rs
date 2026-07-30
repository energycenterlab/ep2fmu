mod config;
mod energyplus_api;

use std::env;
use std::ffi::{CStr, CString};
use std::io::{BufReader, BufWriter};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use config::{InputKind, OutputKind, RuntimeConfig};
use energyplus_api::{EnergyPlusApi, EnergyPlusState};
use ep2fmu_protocol::{read_message, write_message, Command, LogLevel, Response};
use tempfile::TempDir;

static CONTEXT: OnceLock<Mutex<RuntimeContext>> = OnceLock::new();

#[derive(Clone, Copy)]
enum InputHandle {
    Actuator(i32),
    Ems(i32),
}

#[derive(Clone, Copy)]
enum OutputHandle {
    Variable(i32),
    Meter(i32),
}

struct RuntimeContext {
    api: EnergyPlusApi,
    config: RuntimeConfig,
    input_handles: Vec<InputHandle>,
    output_handles: Vec<OutputHandle>,
    current_inputs: Vec<f64>,
    handles_ready: bool,
    ready_sent: bool,
    remaining_steps: u64,
    current_time: f64,
    shutdown: bool,
}

impl RuntimeContext {
    fn send(&self, response: &Response) -> Result<(), String> {
        write_message(&mut BufWriter::new(std::io::stdout().lock()), response)
            .map_err(|error| error.to_string())
    }

    fn receive(&self) -> Result<Command, String> {
        read_message(&mut BufReader::new(std::io::stdin().lock()))
            .map_err(|error| error.to_string())
    }

    unsafe fn ensure_handles(&mut self, state: EnergyPlusState) -> Result<(), String> {
        if self.handles_ready {
            return Ok(());
        }
        if (self.api.api_data_ready)(state) == 0 {
            return Ok(());
        }
        for input in &self.config.inputs {
            let key = EnergyPlusApi::cstring(&input.key, "input key")?;
            let handle = match input.kind {
                InputKind::Actuator => {
                    let component = EnergyPlusApi::cstring(
                        input
                            .component_type
                            .as_deref()
                            .ok_or_else(|| format!("{} has no component_type", input.name))?,
                        "component type",
                    )?;
                    let control = EnergyPlusApi::cstring(
                        input
                            .control_type
                            .as_deref()
                            .ok_or_else(|| format!("{} has no control_type", input.name))?,
                        "control type",
                    )?;
                    InputHandle::Actuator((self.api.get_actuator_handle)(
                        state,
                        component.as_ptr(),
                        control.as_ptr(),
                        key.as_ptr(),
                    ))
                }
                InputKind::Schedule => {
                    let component = CString::new("Schedule:Constant").unwrap();
                    let control = CString::new("Schedule Value").unwrap();
                    InputHandle::Actuator((self.api.get_actuator_handle)(
                        state,
                        component.as_ptr(),
                        control.as_ptr(),
                        key.as_ptr(),
                    ))
                }
                InputKind::EmsGlobal => {
                    InputHandle::Ems((self.api.get_ems_handle)(state, key.as_ptr()))
                }
            };
            let raw = match handle {
                InputHandle::Actuator(value) | InputHandle::Ems(value) => value,
            };
            if raw < 0 {
                return Err(format!(
                    "EnergyPlus handle not found for input {} ({})",
                    input.name, input.key
                ));
            }
            self.input_handles.push(handle);
        }
        for output in &self.config.outputs {
            let handle = match output.kind {
                OutputKind::Variable => {
                    let variable = EnergyPlusApi::cstring(
                        output
                            .variable
                            .as_deref()
                            .ok_or_else(|| format!("{} has no variable", output.name))?,
                        "variable",
                    )?;
                    let key = EnergyPlusApi::cstring(
                        output
                            .key
                            .as_deref()
                            .ok_or_else(|| format!("{} has no key", output.name))?,
                        "variable key",
                    )?;
                    OutputHandle::Variable((self.api.get_variable_handle)(
                        state,
                        variable.as_ptr(),
                        key.as_ptr(),
                    ))
                }
                OutputKind::Meter => {
                    let meter = EnergyPlusApi::cstring(
                        output
                            .meter
                            .as_deref()
                            .ok_or_else(|| format!("{} has no meter", output.name))?,
                        "meter",
                    )?;
                    OutputHandle::Meter((self.api.get_meter_handle)(state, meter.as_ptr()))
                }
            };
            let raw = match handle {
                OutputHandle::Variable(value) | OutputHandle::Meter(value) => value,
            };
            if raw < 0 {
                return Err(format!(
                    "EnergyPlus handle not found for output {}",
                    output.name
                ));
            }
            self.output_handles.push(handle);
        }
        self.handles_ready = true;
        Ok(())
    }

    unsafe fn apply_inputs(&self, state: EnergyPlusState) {
        for (handle, value) in self.input_handles.iter().zip(&self.current_inputs) {
            match handle {
                InputHandle::Actuator(handle) => {
                    (self.api.set_actuator_value)(state, *handle, *value)
                }
                InputHandle::Ems(handle) => (self.api.set_ems_value)(state, *handle, *value),
            }
        }
    }

    unsafe fn outputs(&self, state: EnergyPlusState) -> Vec<f64> {
        self.output_handles
            .iter()
            .map(|handle| match handle {
                OutputHandle::Variable(handle) => (self.api.get_variable_value)(state, *handle),
                OutputHandle::Meter(handle) => (self.api.get_meter_value)(state, *handle),
            })
            .collect()
    }
}

unsafe extern "C" fn begin_zone_timestep(state: EnergyPlusState) {
    let Some(mutex) = CONTEXT.get() else {
        return;
    };
    let mut context = match mutex.lock() {
        Ok(value) => value,
        Err(_) => return,
    };
    if let Err(message) = context.ensure_handles(state) {
        let _ = context.send(&Response::Error { message });
        (context.api.stop_simulation)(state);
        context.shutdown = true;
        return;
    }
    if !context.handles_ready {
        return;
    }
    if (context.api.warmup_flag)(state) != 0 || (context.api.kind_of_sim)(state) != 3 {
        context.apply_inputs(state);
        return;
    }
    if !context.ready_sent {
        let response = Response::Ready {
            outputs: context.outputs(state),
            zone_step_seconds: context.config.zone_step_seconds,
            stop_time_seconds: context.config.stop_time_seconds,
        };
        if context.send(&response).is_err() {
            (context.api.stop_simulation)(state);
            context.shutdown = true;
            return;
        }
        context.ready_sent = true;
    }
    if context.remaining_steps == 0 {
        match context.receive() {
            Ok(Command::Step {
                current_time,
                step_size,
                inputs,
            }) => {
                let tolerance = 1e-9_f64.max(step_size.abs() * 1e-9);
                if (current_time - context.current_time).abs() > tolerance {
                    let _ = context.send(&Response::Error {
                        message: format!(
                            "communication point {current_time} does not match worker time {}",
                            context.current_time
                        ),
                    });
                    return;
                }
                let ratio = step_size / context.config.zone_step_seconds;
                let rounded = ratio.round();
                if step_size <= 0.0 || rounded < 1.0 || (ratio - rounded).abs() > 1e-9 {
                    let _ = context.send(&Response::Error {
                        message: format!(
                            "step {step_size} is not a positive multiple of zone step {}",
                            context.config.zone_step_seconds
                        ),
                    });
                    return;
                }
                if current_time + step_size > context.config.stop_time_seconds + tolerance {
                    let _ = context.send(&Response::Error {
                        message: "step exceeds the EnergyPlus RunPeriod".to_owned(),
                    });
                    return;
                }
                if inputs.len() != context.config.inputs.len() {
                    let _ = context.send(&Response::Error {
                        message: format!(
                            "received {} inputs, expected {}",
                            inputs.len(),
                            context.config.inputs.len()
                        ),
                    });
                    return;
                }
                context.current_inputs = inputs;
                context.remaining_steps = rounded as u64;
            }
            Ok(Command::Shutdown) | Err(_) => {
                (context.api.stop_simulation)(state);
                context.shutdown = true;
                return;
            }
            Ok(Command::Initialize { .. }) => {
                let _ = context.send(&Response::Error {
                    message: "worker is already initialized".to_owned(),
                });
                return;
            }
        }
    }
    context.apply_inputs(state);
}

unsafe extern "C" fn end_zone_timestep(state: EnergyPlusState) {
    let Some(mutex) = CONTEXT.get() else {
        return;
    };
    let mut context = match mutex.lock() {
        Ok(value) => value,
        Err(_) => return,
    };
    if !context.ready_sent
        || context.remaining_steps == 0
        || (context.api.warmup_flag)(state) != 0
        || (context.api.kind_of_sim)(state) != 3
    {
        return;
    }
    context.remaining_steps -= 1;
    context.current_time += context.config.zone_step_seconds;
    if context.remaining_steps == 0 {
        let _ = context.send(&Response::StepComplete {
            time: context.current_time,
            outputs: context.outputs(state),
        });
    }
}

unsafe extern "C" fn energyplus_message(message: *const std::ffi::c_char) {
    if message.is_null() {
        return;
    }
    let text = CStr::from_ptr(message).to_string_lossy().trim().to_owned();
    if text.is_empty() {
        return;
    }
    let level = if text.contains("** Fatal **") || text.contains("** Severe **") {
        LogLevel::Error
    } else if text.contains("** Warning **") {
        LogLevel::Warning
    } else {
        LogLevel::Info
    };
    if let Some(mutex) = CONTEXT.get() {
        if let Ok(context) = mutex.lock() {
            let _ = context.send(&Response::Log {
                level,
                category: "energyplus".to_owned(),
                message: text,
            });
        }
    }
}

fn resolve_home(value: &str) -> Result<PathBuf, String> {
    let candidate = if value.is_empty() {
        env::var("ENERGYPLUS_HOME")
            .map_err(|_| "energyplusHome is empty and ENERGYPLUS_HOME is not set".to_owned())?
    } else {
        value.to_owned()
    };
    let path = PathBuf::from(candidate);
    if !path.is_dir() {
        return Err(format!(
            "EnergyPlus home does not exist: {}",
            path.display()
        ));
    }
    Ok(path)
}

fn run() -> Result<(), String> {
    let initialize: Command =
        read_message(&mut BufReader::new(std::io::stdin().lock())).map_err(|e| e.to_string())?;
    let Command::Initialize {
        resource_dir,
        energyplus_home,
        output_dir,
        keep_outputs,
        run_read_vars,
        instance_name: _,
    } = initialize
    else {
        return Err("first worker command must be initialize".to_owned());
    };
    let resource_dir = PathBuf::from(resource_dir);
    let config = RuntimeConfig::load(&resource_dir)?;
    let home = resolve_home(&energyplus_home)?;

    let mut temp_output: Option<TempDir> = None;
    let output_path = if output_dir.is_empty() {
        let temporary = tempfile::Builder::new()
            .prefix("ep2fmu-runtime-")
            .tempdir()
            .map_err(|error| format!("cannot create output directory: {error}"))?;
        let path = temporary.path().to_path_buf();
        temp_output = Some(temporary);
        path
    } else {
        let path = PathBuf::from(output_dir);
        std::fs::create_dir_all(&path)
            .map_err(|error| format!("cannot create {}: {error}", path.display()))?;
        path
    };

    let api = unsafe { EnergyPlusApi::load(&home)? };
    let state = unsafe { (api.state_new)() };
    if state.is_null() {
        return Err("EnergyPlus stateNew returned null".to_owned());
    }

    for output in &config.outputs {
        if matches!(output.kind, OutputKind::Variable) {
            let variable =
                EnergyPlusApi::cstring(output.variable.as_deref().unwrap_or_default(), "variable")?;
            let key =
                EnergyPlusApi::cstring(output.key.as_deref().unwrap_or_default(), "variable key")?;
            unsafe { (api.request_variable)(state, variable.as_ptr(), key.as_ptr()) };
        }
    }

    let starts = config.inputs.iter().map(|input| input.start).collect();
    CONTEXT
        .set(Mutex::new(RuntimeContext {
            api,
            config: config.clone(),
            input_handles: Vec::new(),
            output_handles: Vec::new(),
            current_inputs: starts,
            handles_ready: false,
            ready_sent: false,
            remaining_steps: 0,
            current_time: 0.0,
            shutdown: false,
        }))
        .map_err(|_| "worker context was already initialized".to_owned())?;

    let context = CONTEXT.get().unwrap().lock().map_err(|e| e.to_string())?;
    let root = EnergyPlusApi::cstring(home.to_string_lossy().as_ref(), "EnergyPlus home")?;
    unsafe {
        (context.api.set_root)(state, root.as_ptr());
        // stdout is the framed IPC channel. Capture EnergyPlus messages and
        // relay them as protocol events instead of allowing raw text on it.
        (context.api.set_console_output)(state, 0);
        (context.api.register_stdout)(state, energyplus_message);
        (context.api.callback_begin)(state, begin_zone_timestep);
        (context.api.callback_end)(state, end_zone_timestep);
    }
    drop(context);

    let mut args = vec![
        "energyplus".to_owned(),
        "-d".to_owned(),
        output_path.to_string_lossy().into_owned(),
    ];
    if run_read_vars {
        args.push("-r".to_owned());
    }
    if let Some(weather) = &config.weather {
        args.push("-w".to_owned());
        args.push(resource_dir.join(weather).to_string_lossy().into_owned());
    }
    args.push(
        resource_dir
            .join(&config.model)
            .to_string_lossy()
            .into_owned(),
    );
    let c_args = args
        .iter()
        .map(|arg| EnergyPlusApi::cstring(arg, "EnergyPlus argument"))
        .collect::<Result<Vec<_>, _>>()?;
    let pointers = c_args.iter().map(|arg| arg.as_ptr()).collect::<Vec<_>>();

    let (run_energyplus, delete_state) = {
        let context = CONTEXT.get().unwrap().lock().map_err(|e| e.to_string())?;
        (context.api.energyplus, context.api.state_delete)
    };
    let result = unsafe { run_energyplus(state, pointers.len() as i32, pointers.as_ptr()) };
    unsafe { delete_state(state) };
    let shutdown = CONTEXT
        .get()
        .unwrap()
        .lock()
        .map_err(|e| e.to_string())?
        .shutdown;

    if keep_outputs {
        if let Some(temporary) = temp_output.take() {
            let retained = temporary.keep();
            eprintln!("ep2fmu outputs retained at {}", retained.display());
        }
    }
    if result != 0 && !shutdown {
        return Err(format!("EnergyPlus exited with status {result}"));
    }
    write_message(
        &mut BufWriter::new(std::io::stdout().lock()),
        &Response::Terminated,
    )
    .map_err(|error| error.to_string())
}

fn main() {
    if let Err(message) = run() {
        let _ = write_message(
            &mut BufWriter::new(std::io::stdout().lock()),
            &Response::Error {
                message: message.clone(),
            },
        );
        eprintln!("ep2fmu worker error: {message}");
        std::process::exit(1);
    }
}

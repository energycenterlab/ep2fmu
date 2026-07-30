use std::ffi::{c_char, c_void, CStr, CString};
use std::path::{Path, PathBuf};

use libloading::Library;

pub const SUPPORTED_ENERGYPLUS_VERSION: &str = match option_env!("EP2FMU_ENERGYPLUS_VERSION") {
    Some(version) => version,
    None => "26.1.0",
};

pub type EnergyPlusState = *mut c_void;
pub type StateCallback = unsafe extern "C" fn(EnergyPlusState);
pub type MessageCallback = unsafe extern "C" fn(*const c_char);

pub struct EnergyPlusApi {
    _library: Library,
    pub state_new: unsafe extern "C" fn() -> EnergyPlusState,
    pub state_delete: unsafe extern "C" fn(EnergyPlusState),
    pub energyplus: unsafe extern "C" fn(EnergyPlusState, i32, *const *const c_char) -> i32,
    pub set_root: unsafe extern "C" fn(EnergyPlusState, *const c_char),
    pub set_console_output: unsafe extern "C" fn(EnergyPlusState, i32),
    pub register_stdout: unsafe extern "C" fn(EnergyPlusState, MessageCallback),
    pub request_variable: unsafe extern "C" fn(EnergyPlusState, *const c_char, *const c_char),
    pub api_data_ready: unsafe extern "C" fn(EnergyPlusState) -> i32,
    pub warmup_flag: unsafe extern "C" fn(EnergyPlusState) -> i32,
    pub kind_of_sim: unsafe extern "C" fn(EnergyPlusState) -> i32,
    pub get_variable_handle:
        unsafe extern "C" fn(EnergyPlusState, *const c_char, *const c_char) -> i32,
    pub get_variable_value: unsafe extern "C" fn(EnergyPlusState, i32) -> f64,
    pub get_meter_handle: unsafe extern "C" fn(EnergyPlusState, *const c_char) -> i32,
    pub get_meter_value: unsafe extern "C" fn(EnergyPlusState, i32) -> f64,
    pub get_actuator_handle:
        unsafe extern "C" fn(EnergyPlusState, *const c_char, *const c_char, *const c_char) -> i32,
    pub set_actuator_value: unsafe extern "C" fn(EnergyPlusState, i32, f64),
    pub get_ems_handle: unsafe extern "C" fn(EnergyPlusState, *const c_char) -> i32,
    pub set_ems_value: unsafe extern "C" fn(EnergyPlusState, i32, f64),
    pub callback_begin: unsafe extern "C" fn(EnergyPlusState, StateCallback),
    pub callback_end: unsafe extern "C" fn(EnergyPlusState, StateCallback),
    pub stop_simulation: unsafe extern "C" fn(EnergyPlusState),
}

unsafe impl Send for EnergyPlusApi {}

impl EnergyPlusApi {
    pub unsafe fn load(home: &Path) -> Result<Self, String> {
        let path = find_library(home).ok_or_else(|| {
            format!(
                "EnergyPlus API library not found under {}; set energyplusHome or ENERGYPLUS_HOME",
                home.display()
            )
        })?;
        let library = Library::new(&path)
            .map_err(|error| format!("cannot load {}: {error}", path.display()))?;

        let version_fn: unsafe extern "C" fn() -> *const c_char = *library
            .get(b"energyPlusVersion\0")
            .map_err(|error| format!("missing energyPlusVersion: {error}"))?;
        let raw_version = version_fn();
        if raw_version.is_null() {
            return Err("EnergyPlus API returned a null version".to_owned());
        }
        let version = CStr::from_ptr(raw_version).to_string_lossy();
        if !is_supported_version(&version) {
            return Err(format!(
                "EnergyPlus {SUPPORTED_ENERGYPLUS_VERSION} is required; loaded {version}"
            ));
        }

        macro_rules! symbol {
            ($name:literal, $ty:ty) => {
                *library
                    .get::<$ty>(concat!($name, "\0").as_bytes())
                    .map_err(|error| format!("missing {}: {error}", $name))?
            };
        }
        Ok(Self {
            state_new: symbol!("stateNew", unsafe extern "C" fn() -> EnergyPlusState),
            state_delete: symbol!("stateDelete", unsafe extern "C" fn(EnergyPlusState)),
            energyplus: symbol!(
                "energyplus",
                unsafe extern "C" fn(EnergyPlusState, i32, *const *const c_char) -> i32
            ),
            set_root: symbol!(
                "setEnergyPlusRootDirectory",
                unsafe extern "C" fn(EnergyPlusState, *const c_char)
            ),
            set_console_output: symbol!(
                "setConsoleOutputState",
                unsafe extern "C" fn(EnergyPlusState, i32)
            ),
            register_stdout: symbol!(
                "registerStdOutCallback",
                unsafe extern "C" fn(EnergyPlusState, MessageCallback)
            ),
            request_variable: symbol!(
                "requestVariable",
                unsafe extern "C" fn(EnergyPlusState, *const c_char, *const c_char)
            ),
            api_data_ready: symbol!(
                "apiDataFullyReady",
                unsafe extern "C" fn(EnergyPlusState) -> i32
            ),
            warmup_flag: symbol!("warmupFlag", unsafe extern "C" fn(EnergyPlusState) -> i32),
            kind_of_sim: symbol!("kindOfSim", unsafe extern "C" fn(EnergyPlusState) -> i32),
            get_variable_handle: symbol!(
                "getVariableHandle",
                unsafe extern "C" fn(EnergyPlusState, *const c_char, *const c_char) -> i32
            ),
            get_variable_value: symbol!(
                "getVariableValue",
                unsafe extern "C" fn(EnergyPlusState, i32) -> f64
            ),
            get_meter_handle: symbol!(
                "getMeterHandle",
                unsafe extern "C" fn(EnergyPlusState, *const c_char) -> i32
            ),
            get_meter_value: symbol!(
                "getMeterValue",
                unsafe extern "C" fn(EnergyPlusState, i32) -> f64
            ),
            get_actuator_handle: symbol!(
                "getActuatorHandle",
                unsafe extern "C" fn(
                    EnergyPlusState,
                    *const c_char,
                    *const c_char,
                    *const c_char,
                ) -> i32
            ),
            set_actuator_value: symbol!(
                "setActuatorValue",
                unsafe extern "C" fn(EnergyPlusState, i32, f64)
            ),
            get_ems_handle: symbol!(
                "getEMSGlobalVariableHandle",
                unsafe extern "C" fn(EnergyPlusState, *const c_char) -> i32
            ),
            set_ems_value: symbol!(
                "setEMSGlobalVariableValue",
                unsafe extern "C" fn(EnergyPlusState, i32, f64)
            ),
            callback_begin: symbol!(
                "callbackBeginZoneTimestepBeforeSetCurrentWeather",
                unsafe extern "C" fn(EnergyPlusState, StateCallback)
            ),
            callback_end: symbol!(
                "callbackEndOfZoneTimeStepAfterZoneReporting",
                unsafe extern "C" fn(EnergyPlusState, StateCallback)
            ),
            stop_simulation: symbol!("stopSimulation", unsafe extern "C" fn(EnergyPlusState)),
            _library: library,
        })
    }

    pub fn cstring(value: &str, label: &str) -> Result<CString, String> {
        CString::new(value).map_err(|_| format!("{label} contains a null byte"))
    }
}

fn is_supported_version(value: &str) -> bool {
    value
        .split(|character: char| !(character.is_ascii_digit() || character == '.'))
        .any(|part| part == SUPPORTED_ENERGYPLUS_VERSION)
}

fn find_library(home: &Path) -> Option<PathBuf> {
    [
        home.join("libenergyplusapi.so"),
        home.join("libenergyplusapi.dylib"),
        home.join("energyplusapi.dll"),
        home.join("EnergyPlusAPI.dll"),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

#[cfg(test)]
mod tests {
    use super::{is_supported_version, SUPPORTED_ENERGYPLUS_VERSION};

    #[test]
    fn accepts_only_the_compiled_energyplus_version() {
        assert!(is_supported_version(SUPPORTED_ENERGYPLUS_VERSION));
        assert!(is_supported_version(&format!(
            "EnergyPlus, Version {SUPPORTED_ENERGYPLUS_VERSION}-abc"
        )));
        assert!(!is_supported_version("0.0.0"));
    }
}

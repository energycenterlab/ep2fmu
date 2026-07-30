use std::path::Path;

use serde::Deserialize;

use crate::energyplus_api::SUPPORTED_ENERGYPLUS_VERSION;

#[derive(Debug, Clone, Deserialize)]
pub struct RuntimeConfig {
    pub schema_version: u32,
    pub energyplus_version: String,
    pub model: String,
    pub weather: Option<String>,
    pub zone_step_seconds: f64,
    pub stop_time_seconds: f64,
    pub inputs: Vec<InputMapping>,
    pub outputs: Vec<OutputMapping>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct InputMapping {
    pub name: String,
    pub kind: InputKind,
    pub key: String,
    pub start: f64,
    pub component_type: Option<String>,
    pub control_type: Option<String>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputKind {
    Actuator,
    Schedule,
    EmsGlobal,
}

#[derive(Debug, Clone, Deserialize)]
pub struct OutputMapping {
    pub name: String,
    pub kind: OutputKind,
    pub key: Option<String>,
    pub variable: Option<String>,
    pub meter: Option<String>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputKind {
    Variable,
    Meter,
}

impl RuntimeConfig {
    pub fn load(resource_dir: &Path) -> Result<Self, String> {
        let path = resource_dir.join("ep2fmu-config.json");
        let bytes = std::fs::read(&path)
            .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
        let config: Self = serde_json::from_slice(&bytes)
            .map_err(|error| format!("invalid {}: {error}", path.display()))?;
        if config.schema_version != 1 {
            return Err(format!(
                "unsupported runtime config schema {}",
                config.schema_version
            ));
        }
        if config.energyplus_version != SUPPORTED_ENERGYPLUS_VERSION {
            return Err(format!(
                "runtime config requires EnergyPlus {}, expected {}",
                config.energyplus_version, SUPPORTED_ENERGYPLUS_VERSION
            ));
        }
        if config.zone_step_seconds <= 0.0 || config.stop_time_seconds <= 0.0 {
            return Err("simulation timing must be positive".to_owned());
        }
        Ok(config)
    }
}

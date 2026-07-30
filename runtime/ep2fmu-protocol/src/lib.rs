//! Length-prefixed MessagePack protocol shared by the FMI library and worker.

use std::io::{Read, Write};

use serde::{de::DeserializeOwned, Deserialize, Serialize};
use thiserror::Error;

const MAX_MESSAGE_SIZE: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "command", rename_all = "snake_case")]
pub enum Command {
    Initialize {
        resource_dir: String,
        energyplus_home: String,
        output_dir: String,
        keep_outputs: bool,
        run_read_vars: bool,
        instance_name: String,
    },
    Step {
        current_time: f64,
        step_size: f64,
        inputs: Vec<f64>,
    },
    Shutdown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "response", rename_all = "snake_case")]
pub enum Response {
    Ready {
        outputs: Vec<f64>,
        zone_step_seconds: f64,
        stop_time_seconds: f64,
    },
    StepComplete {
        time: f64,
        outputs: Vec<f64>,
    },
    Log {
        level: LogLevel,
        category: String,
        message: String,
    },
    Terminated,
    Error {
        message: String,
    },
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LogLevel {
    Debug,
    Info,
    Warning,
    Error,
}

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("MessagePack encode error: {0}")]
    Encode(#[from] rmp_serde::encode::Error),
    #[error("MessagePack decode error: {0}")]
    Decode(#[from] rmp_serde::decode::Error),
    #[error("message length {0} exceeds protocol limit")]
    MessageTooLarge(usize),
}

pub fn write_message<T: Serialize>(
    writer: &mut impl Write,
    message: &T,
) -> Result<(), ProtocolError> {
    let payload = rmp_serde::to_vec_named(message)?;
    if payload.len() > MAX_MESSAGE_SIZE {
        return Err(ProtocolError::MessageTooLarge(payload.len()));
    }
    writer.write_all(&(payload.len() as u32).to_be_bytes())?;
    writer.write_all(&payload)?;
    writer.flush()?;
    Ok(())
}

pub fn read_message<T: DeserializeOwned>(reader: &mut impl Read) -> Result<T, ProtocolError> {
    let mut length = [0_u8; 4];
    reader.read_exact(&mut length)?;
    let length = u32::from_be_bytes(length) as usize;
    if length > MAX_MESSAGE_SIZE {
        return Err(ProtocolError::MessageTooLarge(length));
    }
    let mut payload = vec![0_u8; length];
    reader.read_exact(&mut payload)?;
    Ok(rmp_serde::from_slice(&payload)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_command() {
        let command = Command::Step {
            current_time: 60.0,
            step_size: 120.0,
            inputs: vec![1.5, 2.5],
        };
        let mut bytes = Vec::new();
        write_message(&mut bytes, &command).unwrap();
        assert_eq!(
            read_message::<Command>(&mut bytes.as_slice()).unwrap(),
            command
        );
    }

    #[test]
    fn rejects_oversized_length_before_allocating() {
        let bytes = ((MAX_MESSAGE_SIZE + 1) as u32).to_be_bytes();
        assert!(matches!(
            read_message::<Command>(&mut bytes.as_slice()),
            Err(ProtocolError::MessageTooLarge(_))
        ));
    }
}

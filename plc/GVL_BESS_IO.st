(******************************************************************************
 * Logical I/O image for the BESS controller.
 *
 * Do not assign absolute %MW addresses here until the PLC/Modbus product and
 * its byte/word order are known. Bind these variables in the vendor's I/O or
 * Modbus configuration. See modbus_register_map.csv.
 ******************************************************************************)
VAR_GLOBAL
    (* Commands/status supplied by the PLC application or communication layer. *)
    xBESS_Enable             : BOOL := FALSE;
    xBESS_Reset              : BOOL := FALSE;
    xBESS_MeasurementsValid  : BOOL := FALSE;

    (* Atomic measurement snapshot, all in pu except SOC which is 0..1. *)
    lrBESS_Vpu               : LREAL;
    lrBESS_Pline             : LREAL;
    lrBESS_Qline             : LREAL;
    lrBESS_Loading           : LREAL;
    lrBESS_SOC               : LREAL;
    lrBESS_Pess              : LREAL;
    lrBESS_Qess              : LREAL;

    (* Controller outputs. *)
    lrBESS_Pcmd              : LREAL;
    lrBESS_Qcmd              : LREAL;
    uiBESS_Mode              : USINT;
    lrBESS_SensVP            : LREAL;
    lrBESS_SensVQ            : LREAL;
    lrBESS_InfluenceP        : LREAL;
    lrBESS_InfluenceQ        : LREAL;
    lrBESS_Confidence        : LREAL;
    xBESS_ControllerReady    : BOOL;
END_VAR

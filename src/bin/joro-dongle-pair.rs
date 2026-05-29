// src/bin/joro-dongle-pair.rs
// Last modified: 2026-05-21--0040
//
// Standalone Synapse-free Razer HyperSpeed dongle ↔ Joro pair tool.
//
// Decoded by 3-way capture diff (cap #1+#2+#3 across two physical dongles)
// 2026-05-21. The pair protocol is just 3 commands, byte-identical across
// dongle units. See DONGLE_RE.md §6 for full derivation.
//
//   pair    = 0b:03 args=00 04 00   (discovery on slot 4 = keyboard)
//           + 00:41 args=01 02 da   (bond write: slot=01 type=02-kbd id=0xda-Joro)
//   unpair  = 00:42 args=02 da      (remove bond for type=02 id=0xda)
//
// NO mouse required. NO Synapse required. NO Razer cloud round-trip.
//
// Usage:
//   joro-dongle-pair pair      # Joro must be powered, switch on dongle/BLE position
//   joro-dongle-pair unpair    # remove the Joro bond from the dongle
//   joro-dongle-pair wipe      # convenience alias for `unpair` (clean stale Joro bonds
//                              # on a used dongle before a fresh pair)

// Shared implementation lives in src/dongle_pair.rs so the daemon UI
// can call the exact same functions via IPC. We include it as a module
// via #[path] so both the bin and the main daemon binary use one source.
#[path = "../dongle_pair.rs"]
mod dongle_pair;

use std::env;
use std::process::ExitCode;

fn cmd_pair() -> Result<(), String> {
    eprintln!("[*] running pair (~6s)...");
    let msg = dongle_pair::pair()?;
    eprintln!("[*] {msg}");
    eprintln!();
    eprintln!("    Press any key on the Joro to confirm. If silent:");
    eprintln!("      - keyboard switch on BLE/dongle (not Wired-USB)?");
    eprintln!("      - 3 lights blinking white = ready to pair");
    eprintln!("      - try again — radio discovery can race");
    Ok(())
}

fn cmd_unpair() -> Result<(), String> {
    let msg = dongle_pair::unpair()?;
    eprintln!("[*] {msg}");
    Ok(())
}

fn cmd_wipe() -> Result<(), String> {
    let msg = dongle_pair::wipe()?;
    eprintln!("[i] wipe (double-unpair for ghost bonds): {msg}");
    Ok(())
}

fn print_help() {
    println!("joro-dongle-pair — Synapse-free Razer HyperSpeed dongle ↔ Joro pair tool");
    println!();
    println!("USAGE:");
    println!("    joro-dongle-pair pair      # pair Joro to the connected dongle");
    println!("    joro-dongle-pair unpair    # remove the Joro bond from the dongle");
    println!("    joro-dongle-pair wipe      # clean any stale Joro bonds (used dongle)");
    println!("    joro-dongle-pair help");
    println!();
    println!("Joro prerequisites for pair:");
    println!("  - Hardware switch on the BLE/dongle position (not Wired-USB).");
    println!("  - Keyboard powered on / awake (press any key if dozing).");
    println!("  - No USB cable plugged into Joro.");
    println!();
    println!("Decoded protocol (see DONGLE_RE.md):");
    println!("  pair   = 0b:03 args=00 04 00  +  00:41 args=01 02 da");
    println!("  unpair = 00:42 args=02 da");
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        print_help();
        return ExitCode::from(1);
    }
    let sub = args[1].as_str();
    if matches!(sub, "help" | "-h" | "--help") {
        print_help();
        return ExitCode::from(0);
    }
    let result = match sub {
        "pair" => cmd_pair(),
        "unpair" => cmd_unpair(),
        "wipe" => cmd_wipe(),
        other => {
            eprintln!("unknown subcommand: {other}");
            print_help();
            return ExitCode::from(1);
        }
    };
    match result {
        Ok(()) => ExitCode::from(0),
        Err(e) => {
            eprintln!("ERROR: {e}");
            ExitCode::from(3)
        }
    }
}

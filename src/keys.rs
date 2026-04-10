// src/keys.rs — Key name / VK code / HID usage lookup tables
// Last modified: 2026-04-09--2215

use std::collections::HashMap;
use std::sync::LazyLock;

pub type VkCode = u16;
pub type HidUsage = u8;

/// Raw table of (canonical_name, vk, hid) — canonical names are title-cased
static KEY_TABLE: &[(&str, VkCode, HidUsage)] = &[
    // Letters A-Z: VK 0x41-0x5A, HID 0x04-0x1D
    ("A", 0x41, 0x04), ("B", 0x42, 0x05), ("C", 0x43, 0x06), ("D", 0x44, 0x07),
    ("E", 0x45, 0x08), ("F", 0x46, 0x09), ("G", 0x47, 0x0A), ("H", 0x48, 0x0B),
    ("I", 0x49, 0x0C), ("J", 0x4A, 0x0D), ("K", 0x4B, 0x0E), ("L", 0x4C, 0x0F),
    ("M", 0x4D, 0x10), ("N", 0x4E, 0x11), ("O", 0x4F, 0x12), ("P", 0x50, 0x13),
    ("Q", 0x51, 0x14), ("R", 0x52, 0x15), ("S", 0x53, 0x16), ("T", 0x54, 0x17),
    ("U", 0x55, 0x18), ("V", 0x56, 0x19), ("W", 0x57, 0x1A), ("X", 0x58, 0x1B),
    ("Y", 0x59, 0x1C), ("Z", 0x5A, 0x1D),
    // Digits: VK 0x30-0x39; HID: 0=0x27, 1-9=0x1E-0x26
    ("0", 0x30, 0x27),
    ("1", 0x31, 0x1E), ("2", 0x32, 0x1F), ("3", 0x33, 0x20), ("4", 0x34, 0x21),
    ("5", 0x35, 0x22), ("6", 0x36, 0x23), ("7", 0x37, 0x24), ("8", 0x38, 0x25),
    ("9", 0x39, 0x26),
    // Function keys F1-F12
    ("F1",  0x70, 0x3A), ("F2",  0x71, 0x3B), ("F3",  0x72, 0x3C), ("F4",  0x73, 0x3D),
    ("F5",  0x74, 0x3E), ("F6",  0x75, 0x3F), ("F7",  0x76, 0x40), ("F8",  0x77, 0x41),
    ("F9",  0x78, 0x42), ("F10", 0x79, 0x43), ("F11", 0x7A, 0x44), ("F12", 0x7B, 0x45),
    // Navigation / special
    ("Escape",      0x1B, 0x29),
    ("Enter",       0x0D, 0x28),
    ("Backspace",   0x08, 0x2A),
    ("Tab",         0x09, 0x2B),
    ("Space",       0x20, 0x2C),
    ("CapsLock",    0x14, 0x39),
    ("Insert",      0x2D, 0x49),
    ("Delete",      0x2E, 0x4C),
    ("Home",        0x24, 0x4A),
    ("End",         0x23, 0x4D),
    ("PageUp",      0x21, 0x4B),
    ("PageDown",    0x22, 0x4E),
    ("Up",          0x26, 0x52),
    ("Down",        0x28, 0x51),
    ("Left",        0x25, 0x50),
    ("Right",       0x27, 0x4F),
    ("PrintScreen", 0x2C, 0x46),
    ("ScrollLock",  0x91, 0x47),
    ("Pause",       0x13, 0x48),
    // Punctuation
    ("Grave",       0xC0, 0x35),
    ("Minus",       0xBD, 0x2D),
    ("Equal",       0xBB, 0x2E),
    ("LBracket",    0xDB, 0x2F),
    ("RBracket",    0xDD, 0x30),
    ("Backslash",   0xDC, 0x31),
    ("Semicolon",   0xBA, 0x33),
    ("Quote",       0xDE, 0x34),
    ("Comma",       0xBC, 0x36),
    ("Period",      0xBE, 0x37),
    ("Slash",       0xBF, 0x38),
    // Modifiers
    ("LCtrl",   0xA2, 0xE0),
    ("LShift",  0xA0, 0xE1),
    ("LAlt",    0xA4, 0xE2),
    ("LGui",    0x5B, 0xE3),
    ("RCtrl",   0xA3, 0xE4),
    ("RShift",  0xA1, 0xE5),
    ("RAlt",    0xA5, 0xE6),
    ("RGui",    0x5C, 0xE7),
    // Aliases
    ("LWin",    0x5B, 0xE3),
    ("RWin",    0x5C, 0xE7),
    ("Copilot", 0x86, 0x00),  // Razer Joro "Copilot" key = VK 0x86 (sent with LWin)
    // App key
    ("App", 0x5D, 0x65),
];

/// Map: lowercase key name -> VkCode
static VK_MAP: LazyLock<HashMap<String, VkCode>> = LazyLock::new(|| {
    KEY_TABLE.iter()
        .map(|(name, vk, _hid)| (name.to_lowercase(), *vk))
        .collect()
});

/// Map: lowercase key name -> HidUsage
static HID_MAP: LazyLock<HashMap<String, HidUsage>> = LazyLock::new(|| {
    KEY_TABLE.iter()
        .map(|(name, _vk, hid)| (name.to_lowercase(), *hid))
        .collect()
});

/// Modifier prefix names recognized in combo strings
/// Maps lowercase prefix -> VkCode for the left-hand modifier
static MODIFIER_VK: LazyLock<HashMap<&'static str, VkCode>> = LazyLock::new(|| {
    let mut m = HashMap::new();
    m.insert("ctrl",  0xA2u16); // VK_LCONTROL
    m.insert("shift", 0xA0u16); // VK_LSHIFT
    m.insert("alt",   0xA4u16); // VK_LMENU
    m.insert("win",   0x5Bu16); // VK_LWIN
    m
});

/// Case-insensitive lookup of a key name to its Windows virtual-key code.
pub fn key_name_to_vk(name: &str) -> Option<VkCode> {
    VK_MAP.get(&name.to_lowercase()).copied()
}

/// Case-insensitive lookup of a key name to its HID usage code.
pub fn key_name_to_hid(name: &str) -> Option<HidUsage> {
    HID_MAP.get(&name.to_lowercase()).copied()
}

/// Parse a combo string like "Ctrl+Shift+F12" into (modifier_vks, key_vk).
///
/// Splits on '+', treats leading tokens that match known modifier prefixes as
/// modifiers, and the final token as the key. Returns None if the key token
/// cannot be resolved.
pub fn parse_key_combo(combo: &str) -> Option<(Vec<VkCode>, VkCode)> {
    let parts: Vec<&str> = combo.split('+').collect();
    if parts.is_empty() {
        return None;
    }

    let mut modifiers = Vec::new();
    let key_part = parts[parts.len() - 1]; // last token is the key

    // Walk all tokens except the last; if they match modifier prefixes add them
    for &part in &parts[..parts.len() - 1] {
        let lower = part.to_lowercase();
        if let Some(&vk) = MODIFIER_VK.get(lower.as_str()) {
            modifiers.push(vk);
        } else {
            // Non-modifier token before the last — treat the whole string as unknown
            return None;
        }
    }

    // Resolve the key token via VK map
    let key_vk = key_name_to_vk(key_part)?;
    Some((modifiers, key_vk))
}

/// Returns the HID usage for a single (non-combo) key name.
/// Returns None if the name contains '+' (i.e. it's a combo).
pub fn parse_single_hid_key(name: &str) -> Option<HidUsage> {
    if name.contains('+') {
        return None;
    }
    key_name_to_hid(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_capslock_vk() {
        assert_eq!(key_name_to_vk("CapsLock"), Some(0x14));
    }

    #[test]
    fn test_f12_vk() {
        assert_eq!(key_name_to_vk("F12"), Some(0x7B));
    }

    #[test]
    fn test_escape_vk() {
        assert_eq!(key_name_to_vk("Escape"), Some(0x1B));
    }

    #[test]
    fn test_letter_a_vk() {
        assert_eq!(key_name_to_vk("A"), Some(0x41));
    }

    #[test]
    fn test_unknown_key_vk() {
        assert_eq!(key_name_to_vk("FooBar"), None);
    }

    #[test]
    fn test_case_insensitive_vk() {
        assert_eq!(key_name_to_vk("capslock"), Some(0x14));
        assert_eq!(key_name_to_vk("CAPSLOCK"), Some(0x14));
    }

    #[test]
    fn test_capslock_hid() {
        assert_eq!(key_name_to_hid("CapsLock"), Some(0x39));
    }

    #[test]
    fn test_escape_hid() {
        assert_eq!(key_name_to_hid("Escape"), Some(0x29));
    }

    #[test]
    fn test_f12_hid() {
        assert_eq!(key_name_to_hid("F12"), Some(0x45));
    }

    #[test]
    fn test_letter_a_hid() {
        assert_eq!(key_name_to_hid("A"), Some(0x04));
    }

    #[test]
    fn test_parse_single_key() {
        let (mods, key) = parse_key_combo("Escape").unwrap();
        assert!(mods.is_empty());
        assert_eq!(key, 0x1B);
    }

    #[test]
    fn test_parse_ctrl_f12() {
        let (mods, key) = parse_key_combo("Ctrl+F12").unwrap();
        assert_eq!(mods, vec![0xA2]); // VK_LCONTROL
        assert_eq!(key, 0x7B);        // VK_F12
    }

    #[test]
    fn test_parse_ctrl_shift_f12() {
        let (mods, key) = parse_key_combo("Ctrl+Shift+F12").unwrap();
        assert_eq!(mods.len(), 2);
        assert!(mods.contains(&0xA2)); // VK_LCONTROL
        assert!(mods.contains(&0xA0)); // VK_LSHIFT
        assert_eq!(key, 0x7B);
    }

    #[test]
    fn test_parse_single_hid() {
        assert_eq!(parse_single_hid_key("Escape"), Some(0x29));
        assert_eq!(parse_single_hid_key("LCtrl"), Some(0xE0));
    }

    #[test]
    fn test_parse_single_hid_rejects_combo() {
        assert_eq!(parse_single_hid_key("Ctrl+F12"), None);
    }
}

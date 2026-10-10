// Read-only window/display metadata. No screenshots, input, or accessibility.
import AppKit
import CoreGraphics
import Foundation

func require(_ condition: Bool, _ reason: String) throws {
    if !condition { throw NSError(domain: "qualification-frame", code: 1, userInfo: [NSLocalizedDescriptionKey: reason]) }
}

do {
    try require(CommandLine.arguments.count == 2, "requires owned child PID")
    guard let pid = Int(CommandLine.arguments[1]) else { throw NSError(domain: "qualification-frame", code: 2) }
    let screens = NSScreen.screens
    try require(screens.count == 1, "ambiguous display backing scale: requires one screen")
    let screen = screens[0]
    let scale = screen.backingScaleFactor
    try require(scale.isFinite && scale > 0, "missing actual display backing scale")
    guard let display = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber,
          let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
        throw NSError(domain: "qualification-frame", code: 3, userInfo: [NSLocalizedDescriptionKey: "missing actual display/window inventory"])
    }
    let owned = windows.filter { ($0[kCGWindowOwnerPID as String] as? NSNumber)?.intValue == pid && ($0[kCGWindowLayer as String] as? NSNumber)?.intValue == 0 }
    try require(owned.count == 1, "expected one visible owned native window")
    guard let dictionary = owned[0][kCGWindowBounds as String] as? NSDictionary,
          let bounds = CGRect(dictionaryRepresentation: dictionary) else {
        throw NSError(domain: "qualification-frame", code: 4)
    }
    let displayBounds = CGDisplayBounds(display.uint32Value)
    try require(bounds.width > 0 && bounds.height > 0 && displayBounds.contains(bounds), "owned window is outside the measured display")
    let result: [String: Any] = ["pid": pid, "windowId": owned[0][kCGWindowNumber as String]!, "scale": scale,
        "window": ["x": bounds.minX, "y": bounds.minY, "width": bounds.width, "height": bounds.height],
        "display": ["x": displayBounds.minX, "y": displayBounds.minY, "width": displayBounds.width, "height": displayBounds.height]]
    let bytes = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    print(String(decoding: bytes, as: UTF8.self))
} catch {
    FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
    exit(1)
}

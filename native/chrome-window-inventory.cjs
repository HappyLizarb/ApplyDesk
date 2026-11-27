// macOS JavaScript for Automation. Inspect window metadata without sending Apple events to Chrome.
ObjC.import('AppKit')
ObjC.import('ApplicationServices')

// Accessibility includes minimized windows. Chrome also keeps offscreen drawing surfaces after its last window closes.
function windowInventory(pid) {
    const application = $.AXUIElementCreateApplication(pid)
    const windows = Ref()
    $.AXUIElementSetMessagingTimeout(application, 1)
    if ($.AXUIElementCopyAttributeValue(application, $('AXWindows'), windows) !== 0) return 'unknown'
    const count = Number(ObjC.castRefToObject(windows[0]).count)
    if (!Number.isSafeInteger(count) || count < 0) return 'unknown'
    if (count > 0) return 'occupied'

    const all = ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionAll, $.kCGNullWindowID))
    if (!all || !Number(all.count)) return 'unknown'
    for (let index = 0; index < Number(all.count); index++) {
        const entry = all.objectAtIndex(index)
        if (Number(ObjC.unwrap(entry.objectForKey('kCGWindowOwnerPID'))) !== pid ||
            Number(ObjC.unwrap(entry.objectForKey('kCGWindowLayer'))) !== 0 ||
            !ObjC.unwrap(entry.objectForKey('kCGWindowIsOnscreen'))) continue
        const bounds = entry.objectForKey('kCGWindowBounds')
        if (Number(ObjC.unwrap(bounds.objectForKey('Width'))) > 100 &&
            Number(ObjC.unwrap(bounds.objectForKey('Height'))) > 100) return 'occupied'
    }
    return 'empty'
}

// A missing default-profile PID is conclusive only if the sole other Chrome is ApplyDesk's own UI.
function desktopInventory(pid, interfacePid) {
    const session = ObjC.castRefToObject($.CGSessionCopyCurrentDictionary())
    if (!session || !ObjC.unwrap(session.objectForKey('kCGSSessionOnConsoleKey')) ||
        ObjC.unwrap(session.objectForKey('CGSSessionScreenIsLocked'))) return 'unknown'
    const apps = $.NSRunningApplication.runningApplicationsWithBundleIdentifier('com.google.Chrome')
    const count = Number(apps.count)
    if (!Number.isSafeInteger(count) || count < 0) return 'unknown'
    for (let index = 0; index < count; index++) {
        const current = Number(apps.objectAtIndex(index).processIdentifier)
        if (pid && current === pid && current !== interfacePid) return windowInventory(pid)
        if (!pid && current !== interfacePid) return 'unknown'
    }
    return pid ? 'unknown' : 'empty'
}

// Sample twice so restoring windows and a Chrome process still starting can finish registration.
function run(args) {
    const pid = Number(args[0]), interfacePid = Number(args[1])
    const first = desktopInventory(pid, interfacePid)
    if (first !== 'empty') return first
    $.NSThread.sleepForTimeInterval(0.2)
    return desktopInventory(pid, interfacePid)
}

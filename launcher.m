#import <Cocoa/Cocoa.h>
#include <unistd.h>

// Chrome's private profile lock identifies this workspace, not the user's browser.
static NSRunningApplication *interfaceApplication(NSString *profile) {
    NSString *lock = [profile stringByAppendingPathComponent:@"SingletonLock"];
    NSString *target = [NSFileManager.defaultManager destinationOfSymbolicLinkAtPath:lock error:nil];
    NSInteger pid = [[target.lastPathComponent componentsSeparatedByString:@"-"] lastObject].integerValue;
    if (pid <= 0) return nil;
    NSRunningApplication *application = [NSRunningApplication runningApplicationWithProcessIdentifier:(pid_t)pid];
    return [application.bundleIdentifier isEqualToString:@"com.google.Chrome"] ? application : nil;
}

// Window metadata works without screenshot or Accessibility permissions.
static int focusInterface(NSString *profile) {
    NSRunningApplication *application = interfaceApplication(profile);
    if (!application || application.terminated) return 1;
    NSArray *windows = CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID));
    if (!windows) return 2;
    for (NSDictionary *window in windows) {
        if ([window[(id)kCGWindowOwnerPID] intValue] != application.processIdentifier) continue;
        if ([window[(id)kCGWindowLayer] intValue] != 0) continue;
        NSDictionary *bounds = window[(id)kCGWindowBounds];
        if ([bounds[@"Width"] doubleValue] < 100 || [bounds[@"Height"] doubleValue] < 100) continue;
        return [application activateWithOptions:NSApplicationActivateAllWindows] ? 0 : 2;
    }
    return 1;
}

@interface ApplyDeskLauncher : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSMutableSet<NSTask *> *tasks;
@end

@implementation ApplyDeskLauncher

// Packaged launches use their own resources; source checkouts remain runnable.
- (NSTask *)configuredTask {
    NSBundle *bundle = NSBundle.mainBundle;
    NSString *root = [bundle.resourcePath stringByAppendingPathComponent:@"Program"];
    if (![NSFileManager.defaultManager fileExistsAtPath:[root stringByAppendingPathComponent:@"run.py"]])
        root = bundle.bundlePath.stringByDeletingLastPathComponent;
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/zsh"];
    task.arguments = @[[root stringByAppendingPathComponent:@"Start ApplyDesk.command"]];
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"APPLYDESK_LAUNCHER"] = bundle.executablePath;
    task.environment = environment;
    task.standardInput = NSFileHandle.fileHandleWithNullDevice;
    task.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    return task;
}

// Startup failures remain visible even when Finder provides no terminal.
- (void)showFailure:(NSString *)detail {
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"ApplyDesk could not open";
    alert.informativeText = detail.length ? detail : @"Please check that the app and Python runtime are still available.";
    [alert addButtonWithTitle:@"OK"];
    [alert runModal];
}

// Report a failed child and release its private diagnostic file after exit.
- (void)finished:(NSTask *)task diagnostic:(NSString *)path {
    [self.tasks removeObject:task];
    if (task.terminationStatus != 0) {
        NSString *detail = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
        if (detail.length > 4000) detail = [detail substringFromIndex:detail.length - 4000];
        [self showFailure:detail];
    }
    [NSFileManager.defaultManager removeItemAtPath:path error:nil];
    if (!self.tasks.count) [NSApp terminate:nil];
}

// Each reopen asks the existing workspace to display its window again.
- (void)launchWorkspace {
    NSString *pattern = [NSTemporaryDirectory() stringByAppendingPathComponent:@"applydesk-launch-XXXXXX"];
    char *filename = strdup(pattern.fileSystemRepresentation);
    int descriptor = mkstemp(filename);
    NSString *diagnostic = [NSFileManager.defaultManager stringWithFileSystemRepresentation:filename length:strlen(filename)];
    free(filename);
    if (descriptor < 0) { [self showFailure:@"Cannot create a startup diagnostic file."]; return; }
    NSTask *task = [self configuredTask];
    task.standardError = [[NSFileHandle alloc] initWithFileDescriptor:descriptor closeOnDealloc:YES];
    task.terminationHandler = ^(NSTask *finished) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self finished:finished diagnostic:diagnostic]; });
    };
    NSError *error = nil;
    [self.tasks addObject:task];
    if (![task launchAndReturnError:&error]) {
        [self.tasks removeObject:task];
        [NSFileManager.defaultManager removeItemAtPath:diagnostic error:nil];
        [self showFailure:error.localizedDescription];
        if (!self.tasks.count) [NSApp terminate:nil];
    }
}

// Keep the application responsive to Finder while its local service runs.
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.tasks = [NSMutableSet new];
    [self launchWorkspace];
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)application hasVisibleWindows:(BOOL)visible {
    [self launchWorkspace];
    return NO;
}
@end

// AppKit supplies Finder lifecycle events and visible error dialogs.
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 3 && strcmp(argv[1], "--focus-interface") == 0)
            return focusInterface([NSString stringWithUTF8String:argv[2]]);
        NSApplication *application = NSApplication.sharedApplication;
        ApplyDeskLauncher *delegate = [ApplyDeskLauncher new];
        application.delegate = delegate;
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [application run];
    }
    return 0;
}

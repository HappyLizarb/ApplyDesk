#import "chrome-approval.h"
#import <ApplicationServices/ApplicationServices.h>
#include <poll.h>
#include <unistd.h>

static NSString *const ADTitle = @"Allow remote debugging?";
static NSString *const ADMessage = @"An external app wants full control over this Chrome session to debug it. This includes access to your saved data, cookies and site data, and the ability to navigate to any URL.";
static NSString *const ADWarning = @"Only web developers should turn on this feature, and only use it with trusted apps.";
static NSString *const ADWarningCurrent = @"Only web developers should turn on this feature and only use it with trusted apps.";

// Native dialog text may contain layout whitespace but must retain the exact Chrome wording.
static NSString *normalizedText(NSString *value) {
    if (![value isKindOfClass:NSString.class]) return @"";
    NSArray *parts = [value componentsSeparatedByCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    return [[parts filteredArrayUsingPredicate:[NSPredicate predicateWithFormat:@"length > 0"]] componentsJoinedByString:@" "];
}

// Web content is excluded even if it imitates Chrome's dialog title and Allow control.
static void dialogContents(NSDictionary *node, NSMutableArray *texts, NSMutableArray *buttons) {
    if ([node[@"role"] isEqual:@"AXWebArea"]) return;
    for (NSString *key in @[@"title", @"value", @"description"]) {
        NSString *text = normalizedText(node[key]);
        if (text.length) [texts addObject:text];
    }
    if ([node[@"role"] isEqual:@"AXButton"] && [node[@"title"] isEqual:@"Allow"] && [node[@"enabled"] boolValue])
        [buttons addObject:node];
    for (NSDictionary *child in node[@"children"]) dialogContents(child, texts, buttons);
}

// Only native sheets/dialog windows containing the complete prompt can supply an Allow button.
static void collectApprovals(NSDictionary *node, NSMutableArray *matches) {
    NSString *role = node[@"role"], *subrole = node[@"subrole"];
    if ([role isEqual:@"AXWebArea"]) return;
    BOOL dialog = [role isEqual:@"AXSheet"] || [role isEqual:@"AXDialog"] ||
        ([role isEqual:@"AXWindow"] && [subrole isEqual:@"AXDialog"]);
    if (dialog) {
        NSMutableArray *texts = [NSMutableArray new], *buttons = [NSMutableArray new];
        dialogContents(node, texts, buttons);
        NSString *text = [texts componentsJoinedByString:@" "];
        BOOL knownWarning = [text containsString:ADWarning] || [text containsString:ADWarningCurrent];
        if ([texts containsObject:ADTitle] && [text containsString:ADMessage] && knownWarning && buttons.count == 1)
            [matches addObject:buttons.firstObject];
        return;
    }
    for (NSDictionary *child in node[@"children"]) collectApprovals(child, matches);
}

// More than one candidate stays ambiguous; the caller must never pick the first of several.
NSArray<NSDictionary *> *ADChromeApprovalButtons(NSArray<NSDictionary *> *windows) {
    NSMutableArray *matches = [NSMutableArray new];
    for (NSDictionary *window in windows) collectApprovals(window, matches);
    return matches;
}

// Accessibility reads return nil on unavailable/stale controls without logging page content.
static id attribute(AXUIElementRef element, CFStringRef name) {
    CFTypeRef value = NULL;
    if (AXUIElementCopyAttributeValue(element, name, &value) != kAXErrorSuccess) return nil;
    return CFBridgingRelease(value);
}

// Capture only Chrome's native hierarchy, with a fixed bound for unresponsive or enormous trees.
static NSDictionary *snapshot(AXUIElementRef element, NSUInteger depth, NSUInteger *remaining) {
    if (!*remaining || depth > 15) { *remaining = 0;return @{}; }
    *remaining -= 1;
    NSString *role = attribute(element, kAXRoleAttribute) ?: @"";
    if (!role.length) { *remaining = 0;return @{}; }
    if ([role isEqual:@"AXWebArea"]) return @{@"role": role};
    NSMutableDictionary *node = [@{@"role": role, @"element": (__bridge id)element} mutableCopy];
    NSDictionary *attributes = @{@"subrole": (__bridge NSString *)kAXSubroleAttribute, @"title": (__bridge NSString *)kAXTitleAttribute,
        @"value": (__bridge NSString *)kAXValueAttribute, @"description": (__bridge NSString *)kAXDescriptionAttribute,
        @"enabled": (__bridge NSString *)kAXEnabledAttribute};
    for (NSString *key in attributes) node[key] = attribute(element, (__bridge CFStringRef)attributes[key]);
    NSMutableArray *children = [NSMutableArray new];
    for (id child in attribute(element, kAXChildrenAttribute)) {
        if (!*remaining) break;
        [children addObject:snapshot((__bridge AXUIElementRef)child, depth + 1, remaining)];
    }
    node[@"children"] = children;
    return node;
}

// The endpoint and SingletonLock must identify the same current default-profile Chrome process.
static NSRunningApplication *chromeApplication(NSString *endpoint) {
    NSString *profile = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Google/Chrome"];
    NSString *port = [NSString stringWithContentsOfFile:[profile stringByAppendingPathComponent:@"DevToolsActivePort"] encoding:NSUTF8StringEncoding error:nil];
    NSArray *lines = [[port stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] componentsSeparatedByString:@"\n"];
    if (lines.count != 2 || ![endpoint isEqual:[NSString stringWithFormat:@"ws://127.0.0.1:%@%@", lines[0], lines[1]]]) return nil;
    NSString *lock = [NSFileManager.defaultManager destinationOfSymbolicLinkAtPath:[profile stringByAppendingPathComponent:@"SingletonLock"] error:nil];
    NSInteger pid = [[lock.lastPathComponent componentsSeparatedByString:@"-"] lastObject].integerValue;
    if (pid <= 0) return nil;
    NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:(pid_t)pid];
    return [app.bundleIdentifier isEqual:@"com.google.Chrome"] &&
        [app.bundleURL.path isEqual:@"/Applications/Google Chrome.app"] && !app.terminated ? app : nil;
}

// Never traverse arbitrary app processes or capture webpage content when checking the native prompt.
static NSArray *approvalButtons(NSRunningApplication *app) {
    AXUIElementRef application = AXUIElementCreateApplication(app.processIdentifier);
    AXUIElementSetMessagingTimeout(application, 0.5);
    NSMutableArray *windows = [NSMutableArray new];
    NSUInteger remaining = 500;
    NSArray *nativeWindows = attribute(application, kAXWindowsAttribute);
    for (id window in nativeWindows)
        [windows addObject:snapshot((__bridge AXUIElementRef)window, 0, &remaining)];
    CFRelease(application);
    return nativeWindows && remaining ? ADChromeApprovalButtons(windows) : nil;
}

// Parent stdin is the lease: closure, cancellation, or unexpected input immediately ends approval.
static BOOL readArmCommand(void) {
    struct pollfd input = {STDIN_FILENO, POLLIN | POLLHUP, 0};
    if (poll(&input, 1, 5000) <= 0 || !(input.revents & POLLIN)) return NO;
    char command[8] = {0};
    return read(STDIN_FILENO, command, sizeof(command) - 1) == 4 && strcmp(command, "arm\n") == 0;
}

// Any parent message or process loss revokes the one-shot lease before the native press.
static BOOL approvalCanceled(pid_t parent) {
    struct pollfd input = {STDIN_FILENO, POLLIN | POLLHUP, 0};
    if (getppid() != parent) return YES;
    return poll(&input, 1, 0) != 0;
}

// The helper accepts only a new exact prompt while its initiating CDP attempt is still pending.
static int awaitApproval(NSRunningApplication *app, NSString *endpoint, pid_t parent) {
    NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + 180;
    while (NSProcessInfo.processInfo.systemUptime < deadline && !approvalCanceled(parent)) {
        @autoreleasepool {
            if (chromeApplication(endpoint).processIdentifier != app.processIdentifier) return 0;
            NSArray *matches = approvalButtons(app);
            if (!matches) return 0;
            if (matches.count > 1) return 0;
            if (matches.count == 1) {
                if (approvalCanceled(parent)) return 0;
                AXUIElementRef button = (__bridge AXUIElementRef)matches.firstObject[@"element"];
                AXError result = AXUIElementPerformAction(button, kAXPressAction);
                puts(result == kAXErrorSuccess ? "approved" : "manual");fflush(stdout);
                return 0;
            }
        }
        usleep(100000);
    }
    return 0;
}

// Missing OS permission leaves Chrome's ordinary manual confirmation available without a new prompt.
int ADApproveChromeConnection(NSString *endpoint) {
    if (!AXIsProcessTrusted()) { puts("accessibility_required");return 0; }
    NSRunningApplication *app = chromeApplication(endpoint);
    NSArray *existing = app ? approvalButtons(app) : nil;
    if (!existing || existing.count) { puts("manual");return 0; }
    pid_t parent = getppid();
    puts("ready");fflush(stdout);
    if (!readArmCommand()) return 0;
    return awaitApproval(app, endpoint, parent);
}

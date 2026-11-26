#import "../native-browser.h"
#import "../native-browser-input.h"
#import <Carbon/Carbon.h>
#include <unistd.h>

// Key parsing is pure: these checks never create events or inspect a browser window.
static void keyVocabulary(void) {
    NSDictionary *command = ADNativeKeyDefinition(@"Cmd+A");
    NSCAssert([command[@"keycode"] unsignedShortValue] == kVK_ANSI_A,@"Expected native A key");
    NSCAssert([command[@"flags"] unsignedLongLongValue] == kCGEventFlagMaskCommand,@"Expected Command flag");
    NSCAssert([ADNativeKeyDefinition(@"return") isEqual:ADNativeKeyDefinition(@"enter")],@"Return aliases Enter");
    NSCAssert(ADNativeKeyDefinition(@"cmd+shift+g") != nil,@"Native folder navigation supported");
    NSCAssert(ADNativeKeyDefinition(@"cmd+home") != nil,@"Visible document start supported");
    NSCAssert(ADNativeKeyDefinition(@"ctrl+alt+delete") == nil,@"System shortcut excluded");
    NSCAssert(ADNativeKeyDefinition(@"cmd+n") == nil,@"Window creation is unavailable");
    NSCAssert(ADNativeKeyDefinition(@"javascript:run") == nil,@"No scripting vocabulary");
}

// Invalid requests fail before native scope lookup or any OS input can occur.
static void requestBoundaries(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];state.parentPID = getppid();
    state.pid = 42;state.windowID = 77;state.generation = 3;NSError *error = nil;
    NSCAssert(ADNativeDispatch(state,@{@"method":@"evaluate",@"params":@{}},&error)==nil,@"No script request");
    NSCAssert(error != nil,@"Invalid request explained");error = nil;
    NSCAssert(ADNativeDispatch(state,@{@"method":@"observe",@"params":@[]},&error)==nil,@"Malformed parameters rejected");
    error = nil;
    NSCAssert(ADNativeDispatch(state,@{@"method":@"action",@"params":@{@"pid":@43,@"window_id":@77}},&error)==nil,@"Wrong PID rejected");
    error = nil;
    NSCAssert(ADNativeDispatch(state,@{@"method":@"observe",@"params":@{@"pid":@42,@"window_id":@78}},&error)==nil,@"Wrong window rejected");
    NSDictionary *stopped = ADNativeDispatch(state,@{@"method":@"stop",@"params":@{}},&error);
    NSCAssert([stopped[@"stopped"] boolValue] && state.generation==4,@"Stop revokes generation");
    NSCAssert(state.windowID==77 && state.pid==42,@"Stop retains window identity");
    NSDictionary *closed = ADNativeDispatch(state,@{@"method":@"close",@"params":@{}},&error);
    NSCAssert([closed[@"stopped"] boolValue] && state.windowID==77,@"Close disconnects without closing Chrome");
    NSCAssert(!ADNativeCheckLease(state,&error),@"Stopped lease rejects input");
}

// Permission checks only preflight TCC; they do not prompt or capture any window content.
static void permissionStatus(void) {
    NSDictionary *permissions = ADNativePermissions();
    for (NSString *key in @[@"accessibility",@"input",@"screen_capture",@"chrome_pid"])
        NSCAssert([permissions[key] isKindOfClass:NSNumber.class],@"Structured permission status required");
}

// Final/account controls and implicit Return cannot bypass the backend's reserved-operation path.
static void finalAuthority(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];NSError *error = nil;
    NSDictionary *submit = @{@"label":@"Submit",@"role":@"AXButton",@"web_content":@YES};
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},submit,&error),@"Final click needs authority");
    NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"click",@"final_permit":@YES},submit,&error),@"Backend auth boolean works");
    NSDictionary *reserved = @{@"action":@"click",@"final_permit":@"ab12cd34-attempt"};
    NSCAssert(ADNativeAuthorizeEvent(state,reserved,submit,&error),@"Durable application attempt works");
    NSCAssert(!ADNativeAuthorizeEvent(state,reserved,submit,&error),@"Reserved application cannot be sent twice");
    NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"click"},@{@"label":@"Next"},&error),@"Ordinary progression stays available");
    NSDictionary *field = @{@"label":@"First name",@"role":@"AXTextField",@"web_content":@YES};
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"key",@"key":@"return"},field,&error),@"Implicit form submission needs authority");
    NSDictionary *address = @{@"label":@"Address and search bar",@"role":@"AXTextField",@"web_content":@NO};
    NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"key",@"key":@"return"},address,&error),@"Native address navigation is ordinary input");
}

int main(void) {
    @autoreleasepool { keyVocabulary();requestBoundaries();finalAuthority();permissionStatus();puts("Native browser parsing, finite keys, window identity, Stop persistence, final authority and permission preflight passed."); }
    return 0;
}

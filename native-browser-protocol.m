#import "native-browser.h"
#import "native-browser-window.h"
#include <unistd.h>
#include <poll.h>

// Permission status is read-only and never displays an unsolicited macOS permission prompt.
NSDictionary *ADNativePermissions(void) {
    NSRunningApplication *chrome = ADNativeDefaultChrome();
    return @{@"accessibility":@(AXIsProcessTrusted()),@"input":@(CGPreflightPostEventAccess()),
        @"screen_capture":@(CGPreflightScreenCaptureAccess()),@"chrome_pid":@(chrome ? chrome.processIdentifier : 0)};
}

// The protocol accepts only bounded dictionaries with an explicit finite method vocabulary.
static BOOL requestShape(NSDictionary *request, NSError **error) {
    NSSet *methods = [NSSet setWithArray:@[@"status",@"use_existing",@"attach",@"observe",@"action",@"show",@"stop",@"close"]];
    if (![request isKindOfClass:NSDictionary.class] || ![request[@"method"] isKindOfClass:NSString.class]
        || ![methods containsObject:request[@"method"]] || (request[@"params"] && ![request[@"params"] isKindOfClass:NSDictionary.class])) {
        if (error) *error = ADNativeError(@"Malformed native browser request.");return NO;
    }
    return YES;
}

// Presentation after Stop may raise the existing window but never restores typing/click authority.
static NSDictionary *showStoppedWindow(ADNativeBrowserState *state, NSError **error) {
    BOOL stopped = state.stopped;state.stopped = NO;
    BOOL shown = ADNativeRaise(state,error);
    NSMutableDictionary *result = shown ? [ADNativeObserve(state,NO,error) mutableCopy] : nil;state.stopped = stopped;
    if (result) { result[@"shown"] = @YES;result[@"stopped"] = @(stopped); }return result;
}

// Backend-owned PID/window IDs cannot redirect observations or input into unrelated Chrome windows.
static BOOL sameWindow(ADNativeBrowserState *state, NSDictionary *params, NSError **error) {
    if (![params[@"pid"] isKindOfClass:NSNumber.class] || ![params[@"window_id"] isKindOfClass:NSNumber.class]
        || !state.windowID || [params[@"pid"] intValue]!=state.pid || [params[@"window_id"] unsignedIntValue]!=state.windowID) {
        if (error) *error = ADNativeError(@"The request does not identify the owned Chrome window.");return NO;
    }
    return YES;
}

// Stop/close revoke the helper lease; neither closes Chrome or discards a pending application.
NSDictionary *ADNativeDispatch(ADNativeBrowserState *state, NSDictionary *request, NSError **error) {
    if (!requestShape(request,error)) return nil;
    NSString *method = request[@"method"];NSDictionary *params = request[@"params"] ?: @{};
    if ([method isEqual:@"status"]) return ADNativePermissions();
    if ([@[@"stop",@"close"] containsObject:method]) {
        state.stopped = YES;state.generation += 1;state.observation = nil;
        return @{@"stopped":@YES,@"generation":@(state.generation),@"pid":@(state.pid),@"window_id":@(state.windowID)};
    }
    if ([method isEqual:@"use_existing"]) return ADNativeUseExisting(state,error) ? ADNativeObserve(state,NO,error) : nil;
    if ([method isEqual:@"attach"]) {
        if (![params[@"pid"] isKindOfClass:NSNumber.class] || ![params[@"window_id"] isKindOfClass:NSNumber.class]) { if (error) *error = ADNativeError(@"Owned window identity is required.");return nil; }
        return ADNativeAttach(state,params,error) ? ADNativeObserve(state,NO,error) : nil;
    }
    if (!sameWindow(state,params,error)) return nil;
    if ([method isEqual:@"show"]) return showStoppedWindow(state,error);
    if ([method isEqual:@"observe"]) return ADNativeObserve(state,[params[@"screenshot"] boolValue],error);
    if ([method isEqual:@"action"]) {
        if (![params[@"action"] isKindOfClass:NSString.class] || ![params[@"snapshot_id"] isKindOfClass:NSString.class]
            || ![params[@"generation"] isKindOfClass:NSNumber.class] || (params[@"key"] && ![params[@"key"] isKindOfClass:NSString.class])) {
            if (error) *error = ADNativeError(@"Malformed native input request.");return nil;
        }
        return ADNativeAction(state,params,error);
    }
    if (error) *error = ADNativeError(@"Unknown native browser request.");return nil;
}

// Reading without stdio buffering leaves a queued Stop visible to the character-by-character input lease.
static NSData *readRequestLine(ADNativeBrowserState *state) {
    NSMutableData *line = [NSMutableData new];unsigned char byte;
    while (getppid()==state.parentPID) {
        struct pollfd input = {STDIN_FILENO,POLLIN|POLLHUP,0};
        int available = poll(&input,1,1000);
        if (available<0 || ((input.revents&POLLHUP) && !(input.revents&POLLIN))) return nil;
        if (!available) {
            if (state.windowID && ![[ADNativeChromeWindows(state.pid) valueForKey:(id)kCGWindowNumber] containsObject:@(state.windowID)]) return nil;
            continue;
        }
        if (read(STDIN_FILENO,&byte,1)!=1) return nil;
        if (byte=='\n') return line;
        if (line.length>=262144) return nil;
        [line appendBytes:&byte length:1];
    }
    return nil;
}

// Replies echo only request IDs and approved observation data; unexpected exceptions never reveal payloads.
static void replyRequest(ADNativeBrowserState *state, NSData *line) {
    id request = [NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
    id identifier = [request isKindOfClass:NSDictionary.class] ? request[@"id"] : nil;
    if (![identifier isKindOfClass:NSString.class] && ![identifier isKindOfClass:NSNumber.class]) identifier = NSNull.null;
    NSError *error = nil;NSDictionary *result = nil;
    @try { result = ADNativeDispatch(state,request,&error); }
    @catch (NSException *exception) { state.stopped = YES;error = ADNativeError(@"The native browser request could not be completed safely."); }
    NSMutableDictionary *response = [result ? @{@"id":identifier,@"result":result} : @{@"id":identifier,@"error":error.localizedDescription ?: @"The native browser operation failed."} mutableCopy];
    if (error.userInfo[@"native_code"]) response[@"error_code"] = error.userInfo[@"native_code"];
    NSData *data = [NSJSONSerialization dataWithJSONObject:response options:0 error:nil];
    [NSFileHandle.fileHandleWithStandardOutput writeData:data];
    [NSFileHandle.fileHandleWithStandardOutput writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
}

// A helper process owns one ordinary Chrome window; exiting leaves that window available to the user.
int ADNativeBrowserMain(void) {
    [NSApplication.sharedApplication setActivationPolicy:NSApplicationActivationPolicyAccessory];
    ADNativeBrowserState *state = [ADNativeBrowserState new];state.parentPID = getppid();state.stopped = YES;
    NSData *line;
    while ((line=readRequestLine(state))) {
        @autoreleasepool {
            replyRequest(state,line);
            NSDictionary *request = [NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
            if ([request isKindOfClass:NSDictionary.class] && [request[@"method"] isEqual:@"close"]) break;
        }
    }
    state.stopped = YES;return 0;
}

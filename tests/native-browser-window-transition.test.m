#import "../native-browser-window.h"
#import <objc/runtime.h>
#include <unistd.h>

@interface FixtureChrome : NSObject
@property(nonatomic) pid_t processIdentifier;
@property(nonatomic, getter=isTerminated) BOOL terminated;
@property(nonatomic, strong) NSString *bundleIdentifier;
@property(nonatomic, strong) NSURL *bundleURL;
@end
@implementation FixtureChrome
@end

static FixtureChrome *chrome;
static ADNativeBrowserState *activeState;
static NSArray *stages;
static NSUInteger stage, waits, stopAfter, listReads;
static NSTimeInterval now;
static NSTimeInterval initialReadDelay;

// All process, clock, AX and CG evidence is synthetic; the fixture never enumerates or controls Chrome.
static id fakeApplication(id receiver, SEL selector, pid_t pid) { return chrome; }
static NSString *fakeLock(id receiver, SEL selector, NSString *path, NSError **error) { return @"host-42"; }
static NSTimeInterval fakeClock(id receiver, SEL selector) { return now; }
static BOOL fakeTrust(void) { return YES; }
AXUIElementRef AXUIElementCreateApplication(pid_t pid) { return (AXUIElementRef)CFRetain((__bridge CFTypeRef)@"application"); }
AXError AXUIElementSetMessagingTimeout(AXUIElementRef element, float timeout) { return kAXErrorSuccess; }
AXError AXUIElementGetPid(AXUIElementRef element, pid_t *pid) {
    NSDictionary *node = stages[stage][@"nodes"][(__bridge id)element];
    if (!node || [node[@"expired"] boolValue]) return kAXErrorInvalidUIElement;
    *pid = [node[@"pid"] intValue];return kAXErrorSuccess;
}

// Position and size remain actual AXValue objects so production geometry parsing is exercised.
AXError AXUIElementCopyAttributeValue(AXUIElementRef element, CFStringRef key, CFTypeRef *value) {
    id identity = (__bridge id)element;*value = NULL;
    if ([identity isEqual:@"application"] && CFEqual(key,kAXWindowsAttribute)) {
        listReads += 1;*value = CFRetain((__bridge CFTypeRef)[stages[stage][@"nodes"] allKeys]);
    } else {
        NSDictionary *node = stages[stage][@"nodes"][identity];CGRect rect = [node[@"bounds"] rectValue];
        if (!node || [node[@"expired"] boolValue]) return kAXErrorInvalidUIElement;
        if (CFEqual(key,kAXRoleAttribute)) *value = CFRetain((__bridge CFTypeRef)node[@"role"]);
        else if (CFEqual(key,kAXPositionAttribute)) {
            now += initialReadDelay;initialReadDelay = 0;
            *value = AXValueCreate(kAXValueCGPointType,&rect.origin);
        }
        else if (CFEqual(key,kAXSizeAttribute)) *value = AXValueCreate(kAXValueCGSizeType,&rect.size);
    }
    return *value ? kAXErrorSuccess : kAXErrorAttributeUnsupported;
}

// Every retry advances controlled native evidence and can stop input or remove the owned process/window.
static CFArrayRef fakeWindows(CGWindowListOption options, CGWindowID relative) {
    return CFRetain((__bridge CFArrayRef)stages[stage][@"cg"]);
}
static int fakeSleep(useconds_t duration) {
    waits += 1;now += duration/1000000.0;
    if (stage+1<stages.count) stage += 1;
    if (stopAfter && waits>=stopAfter) activeState.stopped = YES;
    if ([stages[stage][@"process_lost"] boolValue]) chrome.processIdentifier = 99;
    return 0;
}

#define CGWindowListCopyWindowInfo fakeWindows
#define AXIsProcessTrusted fakeTrust
#define usleep fakeSleep
#include "../native-browser-window.m"
#undef usleep
#undef AXIsProcessTrusted
#undef CGWindowListCopyWindowInfo

// The captured native transition moved the proven AX window ninety points before CG caught up.
static NSDictionary *evidence(CGFloat cgX, CGFloat axX, BOOL owned) {
    CGRect cg = CGRectMake(cgX,56,839,954), ax = CGRectMake(axX,56,839,954);
    NSDictionary *window = @{(id)kCGWindowNumber:@7,(id)kCGWindowOwnerPID:@42,(id)kCGWindowLayer:@0,
        (id)kCGWindowIsOnscreen:@YES,(id)kCGWindowBounds:CFBridgingRelease(CGRectCreateDictionaryRepresentation(cg))};
    NSDictionary *nodes = @{@"proven":@{@"pid":@42,@"role":@"AXWindow",@"bounds":[NSValue valueWithRect:ax]},
        @"unrelated":@{@"pid":@42,@"role":@"AXWindow",@"bounds":[NSValue valueWithRect:cg]}};
    return @{@"cg":owned ? @[window] : @[],@"nodes":nodes};
}

// Each case begins with one previously established exact CG/AX pair, not a geometry-only guess.
static void resetFixture(NSArray *sequence) {
    chrome = [FixtureChrome new];chrome.processIdentifier = 42;chrome.bundleIdentifier = @"com.google.Chrome";
    chrome.bundleURL = [NSURL fileURLWithPath:@"/Applications/Google Chrome.app"];
    activeState = [ADNativeBrowserState new];activeState.pid = 42;activeState.parentPID = getppid();
    activeState.windowID = 7;activeState.windowElementID = 7;activeState.windowElement = @"proven";
    activeState.bounds = CGRectMake(242,56,839,954);
    stages = sequence;stage = 0;waits = 0;stopAfter = 0;listReads = 0;now = 100;initialReadDelay = 0;
}

// Both directions of asynchronous movement recover using fresh samples of only the retained identity.
static void movingWindow(void) {
    resetFixture(@[evidence(242,332,YES),evidence(242,332,YES),evidence(332,332,YES)]);
    NSCAssert(ADNativeValidateWindow(activeState,NO,nil) && waits==2,@"AX-first movement settles after fresh CG samples");
    NSCAssert([activeState.windowElement isEqual:@"proven"] && activeState.bounds.origin.x==332 && listReads==0,@"Unrelated old-position AX identity is never adopted");
    resetFixture(@[evidence(332,242,YES),evidence(332,332,YES)]);
    NSCAssert(ADNativeValidateWindow(activeState,NO,nil) && waits==1,@"CG-first movement also settles");
    NSCAssert(ADNativeValidateWindow(activeState,NO,nil) && waits==1,@"A settled warm check adds no wait");
    resetFixture(@[evidence(242,332,YES),evidence(332,332,YES)]);initialReadDelay = 1;
    NSCAssert(ADNativeValidateWindow(activeState,NO,nil) && waits==1,@"A blocking first AX query cannot consume the mismatch retry grace");
    NSCAssert(now>=101 && now<101.1 && listReads==0,@"One fresh sample recovers after the slow native query without replacing identity");
}

// Stop, process replacement and closed/hidden windows remain failures during a transition.
static void interruptedWindow(void) {
    resetFixture(@[evidence(242,332,YES),evidence(332,332,YES)]);stopAfter = 1;NSError *error = nil;
    NSCAssert(!ADNativeValidateWindow(activeState,NO,&error) && activeState.stopped && waits==1,@"Stop revokes resampling");
    resetFixture(@[evidence(242,332,YES),evidence(332,332,NO)]);error = nil;
    NSCAssert(!ADNativeValidateWindow(activeState,NO,&error) && [error.userInfo[@"native_code"] isEqual:@"window_closed"],@"Owned CG loss fails closed");
    NSMutableDictionary *replaced = [evidence(332,332,YES) mutableCopy];replaced[@"process_lost"] = @YES;
    resetFixture(@[evidence(242,332,YES),replaced]);error = nil;
    NSCAssert(!ADNativeValidateWindow(activeState,NO,&error) && [error.userInfo[@"native_code"] isEqual:@"window_closed"],@"Process replacement cannot retain ownership");
    NSCAssert([activeState.windowElement isEqual:@"proven"] && activeState.windowElementID==7,@"Failure never substitutes or discards proven AX identity");
}

// Persistent mismatch or expired AX references time out without enumerating an alternative same-position window.
static void unavailableIdentity(void) {
    resetFixture(@[evidence(242,332,YES)]);
    NSCAssert(!ADNativeValidateWindow(activeState,NO,nil) && now>=100.6 && now<100.63,@"Persistent movement has a bounded deadline");
    NSCAssert(listReads==0 && [activeState.windowElement isEqual:@"proven"],@"Timeout preserves provenance without switching windows");
    NSMutableDictionary *expired = [evidence(242,242,YES) mutableCopy];NSMutableDictionary *nodes = [expired[@"nodes"] mutableCopy];
    NSMutableDictionary *proven = [nodes[@"proven"] mutableCopy];proven[@"expired"] = @YES;nodes[@"proven"] = proven;expired[@"nodes"] = nodes;
    resetFixture(@[expired]);
    NSCAssert(!ADNativeValidateWindow(activeState,NO,nil) && listReads==0,@"Expired AX reference is not replaced by an unrelated matching window");
}

int main(void) {
    @autoreleasepool {
        method_setImplementation(class_getClassMethod(NSRunningApplication.class,@selector(runningApplicationWithProcessIdentifier:)),(IMP)fakeApplication);
        method_setImplementation(class_getInstanceMethod(NSFileManager.class,@selector(destinationOfSymbolicLinkAtPath:error:)),(IMP)fakeLock);
        method_setImplementation(class_getInstanceMethod(object_getClass(NSProcessInfo.processInfo),@selector(systemUptime)),(IMP)fakeClock);
        movingWindow();interruptedWindow();unavailableIdentity();
        puts("Native window transition: AX/CG movement, exact retained identity, Stop, process/window loss, bounded failure and expired AX passed without UI.");
    }
    return 0;
}

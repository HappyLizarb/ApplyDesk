#import "../native-browser.h"
#include <unistd.h>

static NSUInteger probeCount, failures;
static AXUIElementRef fixtureHit;
static ADNativeBrowserState *stoppedDuringProbe;
static NSString *fixtureRole = @"AXButton";
static CGSize fixtureSize;

// Role and geometry fixtures distinguish one-point text strips from ordinary native controls.
AXError AXUIElementCopyAttributeValue(AXUIElementRef element, CFStringRef attribute, CFTypeRef *value) {
    if (CFEqual(attribute,kAXRoleAttribute)) { *value = CFRetain((__bridge CFTypeRef)fixtureRole);return kAXErrorSuccess; }
    if (CFEqual(attribute,kAXSizeAttribute)) { *value = AXValueCreate(kAXValueCGSizeType,&fixtureSize);return kAXErrorSuccess; }
    *value = NULL;return kAXErrorAttributeUnsupported;
}

// Substitute only the native hit response; these fixtures never inspect a real window or post input.
AXError AXUIElementCopyElementAtPosition(AXUIElementRef element, float x, float y, AXUIElementRef *result) {
    probeCount += 1;
    if (stoppedDuringProbe) stoppedDuringProbe.stopped = YES;
    if (probeCount<=failures) { *result = NULL;return kAXErrorCannotComplete; }
    *result = (AXUIElementRef)CFRetain(fixtureHit);
    return kAXErrorSuccess;
}

// The owned-window identity is an isolated AX token for this test process, never a browser surface.
static ADNativeBrowserState *fixtureState(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];state.parentPID = getppid();
    state.pid = getpid();state.bounds = CGRectMake(0,0,200,200);
    state.windowElement = (__bridge id)fixtureHit;
    return state;
}

// Approximate first hits recover after the cache settles; persistent misses remain rejected.
static void boundedHitRetries(void) {
    ADNativeBrowserState *state = fixtureState();CGPoint point = CGPointMake(100,100);
    probeCount = 0;failures = 1;
    NSCAssert(ADNativeHitOwned(state,point,fixtureHit),@"Initially missing exact hit can settle at the same point");
    NSCAssert(probeCount==2,@"Stop probing immediately after a verified hit");
    probeCount = 0;failures = 0;
    NSCAssert(ADNativeHitOwned(state,point,fixtureHit) && probeCount==1,@"Already exact hits incur no retry");
    probeCount = 0;failures = 100;
    NSCAssert(!ADNativeHitOwned(state,point,fixtureHit) && probeCount==4,@"Persistent misses have a bounded rejection");
    probeCount = 0;
    NSCAssert(!ADNativeHitOwned(state,CGPointMake(300,100),fixtureHit) && probeCount==0,@"Outside-window points never probe");
}

// A settled hit still needs the same owned process, and Stop interrupts the retry sequence.
static void ownershipAndStop(void) {
    ADNativeBrowserState *state = fixtureState();CGPoint point = CGPointMake(100,100);
    probeCount = 0;failures = 0;state.pid = getpid()+1;
    NSCAssert(!ADNativeHitOwned(state,point,fixtureHit) && probeCount==4,@"A different process never becomes an owned hit");
    state = fixtureState();probeCount = 0;failures = 1;stoppedDuringProbe = state;
    NSCAssert(!ADNativeHitOwned(state,point,fixtureHit) && probeCount==1,@"Stop cancels before another native hit probe");
    stoppedDuringProbe = nil;
}

// One-point static text keeps an immediate exact hit but skips waits after a failed first hit.
static void thinStaticTextPolicy(void) {
    ADNativeBrowserState *state = fixtureState();CGPoint point = CGPointMake(100,100);fixtureRole = @"AXStaticText";
    for (NSValue *size in @[[NSValue valueWithSize:CGSizeMake(200,1)],[NSValue valueWithSize:CGSizeMake(1,19)]]) {
        fixtureSize = size.sizeValue;probeCount = 0;failures = 1;
        NSCAssert(!ADNativeHitOwned(state,point,fixtureHit) && probeCount==1,@"Failed one-point text hit does not schedule retries");
        probeCount = 0;failures = 0;
        NSCAssert(ADNativeHitOwned(state,point,fixtureHit) && probeCount==1,@"Immediately verified thin text stays readable");
    }
    for (NSValue *size in @[[NSValue valueWithSize:CGSizeMake(238,19)],[NSValue valueWithSize:CGSizeMake(200,2)],[NSValue valueWithSize:CGSizeZero]]) {
        fixtureSize = size.sizeValue;probeCount = 0;failures = 1;
        NSCAssert(ADNativeHitOwned(state,point,fixtureHit) && probeCount==2,@"Readable text and unknown sizes retain stabilization");
    }
    fixtureRole = @"AXTextField";fixtureSize = CGSizeMake(200,1);probeCount = 0;failures = 1;
    NSCAssert(ADNativeHitOwned(state,point,fixtureHit) && probeCount==2,@"Controls keep retries regardless of thin geometry");
    fixtureRole = @"AXStaticText";probeCount = 0;failures = 1;
    NSCAssert(ADNativeHitOwned(state,point,NULL) && probeCount==2,@"Viewport scrolling has no text-target shortcut");
    fixtureRole = @"AXButton";
}

int main(void) {
    @autoreleasepool {
        fixtureHit = AXUIElementCreateApplication(getpid());
        boundedHitRetries();ownershipAndStop();thinStaticTextPolicy();CFRelease(fixtureHit);
        puts("Native hit settling, one-point static text, controls, viewport scope and Stop checks passed without browser reads or input.");
    }
    return 0;
}

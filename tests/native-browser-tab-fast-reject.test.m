#import "../native/native-browser.h"
#include <unistd.h>

static NSString *fixtureRole, *fixtureSubrole;
static NSUInteger attributeReads, unrelatedReads;

// Native attribute responses are substituted; the test never reads a real window or its contents.
AXError AXUIElementCopyAttributeValue(AXUIElementRef element, CFStringRef attribute, CFTypeRef *value) {
    attributeReads += 1;id result = nil;
    if (CFEqual(attribute,kAXRoleAttribute)) result = fixtureRole;
    else if (CFEqual(attribute,kAXSubroleAttribute)) result = fixtureSubrole;
    else unrelatedReads += 1;
    if (!result) { *value = NULL;return kAXErrorAttributeUnsupported; }
    *value = CFRetain((__bridge CFTypeRef)result);return kAXErrorSuccess;
}

// Ordinary webpage hits cannot require native tab parent, child, focus or geometry requests.
static void rejectNonTabsEarly(AXUIElementRef token) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];
    for (NSString *role in @[@"AXStaticText",@"AXHeading",@"AXTextField",@"AXButton"]) {
        fixtureRole = role;fixtureSubrole = @"";attributeReads = unrelatedReads = 0;
        NSCAssert(!ADNativeTabOverlayHit(state,CGPointZero,token,token),@"Non-tab cannot use native tab exception");
        NSCAssert(attributeReads==1 && unrelatedReads==0,@"Non-tab rejects after its role without extra AX reads");
    }
    fixtureRole = @"AXRadioButton";fixtureSubrole = @"";attributeReads = unrelatedReads = 0;
    NSCAssert(!ADNativeTabOverlayHit(state,CGPointZero,token,token),@"Web radio button cannot use native tab exception");
    NSCAssert(attributeReads==2 && unrelatedReads==0,@"Wrong subrole rejects before ancestry and geometry reads");
}

int main(void) {
    @autoreleasepool {
        AXUIElementRef token = AXUIElementCreateApplication(getpid());rejectNonTabsEarly(token);CFRelease(token);
        puts("Native tab fast rejection preserves exclusions while avoiding unrelated accessibility reads.");
    }
    return 0;
}

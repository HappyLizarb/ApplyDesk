#import "../native-browser-input.h"
#import "../native-browser-window.h"
#import <Carbon/Carbon.h>
#include <unistd.h>

static ADNativeBrowserState *fixtureState;
static id fixtureFocus;
static NSMutableString *retainedValue;
static NSUInteger clockMicros, selectedStart, selectedLength, selectionDue, unicodeEvents, selectionWaits;
static NSUInteger revokeAt, loseFocusAt, chordEvents, backspaceEvents;
static NSUInteger pathWrites;

// The fixture models the captured 22ms deferred Cmd-A selection, without posting any system event.
static void fixturePost(CGEventTapLocation tap, CGEventRef event) {
    if (CGEventGetType(event)!=kCGEventKeyDown) return;
    if (CGEventGetFlags(event)&kCGEventFlagMaskCommand) {
        chordEvents += 1;selectionDue = clockMicros+22000;return;
    }
    if (CGEventGetIntegerValueField(event,kCGKeyboardEventKeycode)==kVK_Delete) {
        backspaceEvents += 1;
        [retainedValue deleteCharactersInRange:NSMakeRange(selectedStart,selectedLength)];selectedLength = 0;return;
    }
    UniChar characters[128];UniCharCount count = 0;
    CGEventKeyboardGetUnicodeString(event,128,&count,characters);
    if (!count) return;
    NSString *text = [NSString stringWithCharacters:characters length:count];unicodeEvents += 1;
    [retainedValue replaceCharactersInRange:NSMakeRange(selectedStart,selectedLength) withString:text];
    selectedStart += text.length;selectedLength = 0;
}

// Virtual waits make selection completion, Stop and focus loss deterministic rather than timing-sensitive.
static int fixtureSleep(useconds_t delay) {
    clockMicros += delay;
    if (delay==30000) selectionWaits += 1;
    if (selectionDue && clockMicros>=selectionDue) {
        selectedStart = 0;selectedLength = retainedValue.length;selectionDue = 0;
    }
    if (revokeAt && clockMicros>=revokeAt) fixtureState.stopped = YES;
    if (loseFocusAt && clockMicros>=loseFocusAt) fixtureFocus = @"different-control";
    return 0;
}

// Only these input-helper dependencies are replaced; the real select/type implementation is included below.
static BOOL fixtureLease(ADNativeBrowserState *state, NSError **error) {
    if (state.stopped && error) *error = ADNativeError(@"Native browser input stopped.");
    return !state.stopped;
}
static BOOL fixtureValidate(ADNativeBrowserState *state, BOOL front, NSError **error) {
    return fixtureLease(state,error);
}
static id fixtureAttribute(AXUIElementRef element, CFStringRef name) {
    if (CFEqual(name,kAXIdentifierAttribute)) return @"PathTextField";
    if (CFEqual(name,kAXValueAttribute)) return retainedValue;
    return CFEqual(name,kAXFocusedUIElementAttribute) ? fixtureFocus : nil;
}
// The file chooser records an assignment; writing the remembered path again would restart completion.
static AXError fixtureSetValue(AXUIElementRef element, CFStringRef name, CFTypeRef value) {
    if (!CFEqual(name,kAXValueAttribute)) return kAXErrorAttributeUnsupported;
    [retainedValue setString:(__bridge NSString *)value];
    pathWrites += 1;
    return kAXErrorSuccess;
}
static AXUIElementRef fixtureApplication(pid_t pid) {
    return (AXUIElementRef)CFRetain((__bridge CFTypeRef)@"fixture-application");
}
static bool fixturePermission(void) { return true; }
static uint32_t fixtureJitter(uint32_t upper) { return 15000; }

#define CGEventPost fixturePost
#define CGPreflightPostEventAccess fixturePermission
#define usleep fixtureSleep
#define arc4random_uniform fixtureJitter
#define ADNativeCheckLease fixtureLease
#define ADNativeValidateWindow fixtureValidate
#define ADNativeAttribute fixtureAttribute
#define AXUIElementCreateApplication fixtureApplication
#define AXUIElementSetAttributeValue fixtureSetValue
#include "../native-browser-input.m"
#undef AXUIElementSetAttributeValue
#undef AXUIElementCreateApplication
#undef ADNativeAttribute
#undef ADNativeValidateWindow
#undef ADNativeCheckLease
#undef arc4random_uniform
#undef usleep
#undef CGPreflightPostEventAccess
#undef CGEventPost

// A stable dummy field starts with the same seeded value and cursor position as the live local fixture.
static NSDictionary *resetFixture(void) {
    fixtureState = [ADNativeBrowserState new];fixtureState.pid = getpid();fixtureFocus = @"dummy-control";
    fixtureState.elements = [@{@"dummy":fixtureFocus} mutableCopy];
    retainedValue = [@"Seed 1234567890" mutableCopy];selectedStart = retainedValue.length;selectedLength = 0;
    clockMicros = 0;selectionDue = 0;unicodeEvents = 0;selectionWaits = 0;chordEvents = 0;backspaceEvents = 0;revokeAt = 0;loseFocusAt = 0;
    return @{@"id":@"dummy",@"role":@"AXTextField",@"bounds":@{@"x":@0,@"y":@0,@"width":@200,@"height":@30}};
}

// The observed event-order bug is reproduced first, then the production replacement path must prevent it.
static void delayedSelection(void) {
    NSDictionary *control = resetFixture();
    NSCAssert(ADNativePostKey(fixtureState,kVK_ANSI_A,kCGEventFlagMaskCommand,nil),@"Dummy chord sent");
    NSCAssert(typeCharacters(fixtureState,fixtureFocus,@"Az",nil),@"Dummy characters delivered");
    NSCAssert([retainedValue isEqual:@"z"],@"Unsettled select-all erases the already delivered first character");
    resetFixture();
    NSCAssert(typeTarget(fixtureState,@{@"text":@"Az",@"replace":@YES},control,nil)!=nil,@"Guarded replacement succeeds");
    NSCAssert([retainedValue isEqual:@"Az"] && unicodeEvents==2 && selectionWaits==5,@"All characters follow completed selection");
}

// Cancellation or focus loss during settling prevents every subsequent Unicode event.
static void revokedSelection(void) {
    NSDictionary *control = resetFixture();revokeAt = 60000;NSError *error = nil;
    NSCAssert(typeTarget(fixtureState,@{@"text":@"Az",@"replace":@YES},control,&error)==nil,@"Stop interrupts settling");
    NSCAssert(unicodeEvents==0 && selectionWaits==2 && error!=nil,@"Stop is observed within one 30ms interval");
    resetFixture();loseFocusAt = 60000;error = nil;
    NSCAssert(typeTarget(fixtureState,@{@"text":@"Az",@"replace":@YES},control,&error)==nil,@"Lost native focus prevents text");
    NSCAssert(unicodeEvents==0 && [error.localizedDescription containsString:@"after selecting"],@"Focus is rechecked before first character");
}

// Insertion bypasses selection and its wait; already selected text still waits for the newly posted chord.
static void unchangedInsertion(void) {
    NSDictionary *control = resetFixture();
    NSCAssert(typeTarget(fixtureState,@{@"text":@"Az",@"replace":@NO},control,nil)!=nil,@"Ordinary insertion succeeds");
    NSCAssert([retainedValue isEqual:@"Seed 1234567890Az"] && chordEvents==0 && selectionWaits==0,@"replace:false remains unchanged");
    resetFixture();selectedStart = 0;selectedLength = retainedValue.length;
    NSCAssert(typeTarget(fixtureState,@{@"text":@"Az",@"replace":@YES},control,nil)!=nil,@"Selected text still settles pending shortcut");
    NSCAssert([retainedValue isEqual:@"Az"] && selectionWaits==5,@"Existing selection is not a premature readiness shortcut");
}

// An empty replacement deliberately clears; empty insertion and interrupted clearing cannot delete data.
static void emptyReplacement(void) {
    NSDictionary *control = resetFixture();
    NSCAssert(typeTarget(fixtureState,@{@"text":@"",@"replace":@YES},control,nil)!=nil,@"Empty replacement succeeds");
    NSCAssert(retainedValue.length==0 && backspaceEvents==1 && selectionWaits==5 && unicodeEvents==0,@"One Backspace follows guarded full selection");
    resetFixture();
    NSCAssert(typeTarget(fixtureState,@{@"text":@"",@"replace":@NO},control,nil)!=nil,@"Empty insertion succeeds without editing");
    NSCAssert([retainedValue isEqual:@"Seed 1234567890"] && !backspaceEvents && !chordEvents && !selectionWaits,@"Empty insertion remains a no-op");
    resetFixture();revokeAt = 60000;
    NSCAssert(typeTarget(fixtureState,@{@"text":@"",@"replace":@YES},control,nil)==nil && !backspaceEvents,@"Stop prevents clearing");
    resetFixture();loseFocusAt = 60000;
    NSCAssert(typeTarget(fixtureState,@{@"text":@"",@"replace":@YES},control,nil)==nil && !backspaceEvents,@"Focus loss prevents clearing");
}

// Both a new and a remembered chooser path must verify without posting characters or reassigning the same value.
static void repeatedChooserPath(void) {
    NSMutableDictionary *field = [resetFixture() mutableCopy];
    field[@"parent_dialog_id"] = @"native-chooser";
    NSDictionary *args = @{@"text":@"/fixture/original.pdf",@"replace":@YES,@"private":@YES};
    pathWrites = 0;
    for (int pass=0; pass<2; pass++)
        NSCAssert([typeTarget(fixtureState,args,field,nil)[@"private_value_matches"] boolValue],@"Chooser preserved exact path");
    NSCAssert(pathWrites==1 && unicodeEvents==0,@"The repeated path requires no input");
}

int main(void) {
    @autoreleasepool { delayedSelection();revokedSelection();unchangedInsertion();emptyReplacement();repeatedChooserPath();puts("Verified native text replacement, chooser reuse, and focus loss."); }
    return 0;
}

#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>

@interface ADNativeBrowserState : NSObject
@property(nonatomic) pid_t pid;
@property(nonatomic) pid_t parentPID;
@property(nonatomic) CGWindowID windowID;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) BOOL stopped;
@property(nonatomic) BOOL performingInput;
@property(nonatomic) CGRect bounds;
@property(nonatomic, strong) id windowElement;
@property(nonatomic) CGWindowID windowElementID;
@property(nonatomic, strong) NSMutableDictionary<NSString *, id> *elements;
@property(nonatomic, strong) NSMutableDictionary<NSString *, id> *stableElements;
@property(nonatomic) NSUInteger nextElement;
@property(nonatomic, strong) NSMutableArray<NSString *> *redactions;
@property(nonatomic, strong) NSMutableSet<NSString *> *usedFinalPermits;
@property(nonatomic, strong) NSDictionary *observation;
@property(nonatomic, strong) NSArray<NSDictionary *> *nativeSurfaces;
@end

// Shared helpers never echo typed data, credentials or unrelated window contents.
NSError *ADNativeError(NSString *message);
id ADNativeAttribute(AXUIElementRef element, CFStringRef attribute);
CGRect ADNativeElementBounds(AXUIElementRef element);
NSDictionary *ADNativeRect(CGRect rect);
BOOL ADNativeCheckLease(ADNativeBrowserState *state, NSError **error);
BOOL ADNativeValidateWindow(ADNativeBrowserState *state, BOOL requireFront, NSError **error);
BOOL ADNativeHitOwned(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected);
NSDictionary *ADNativeObserve(ADNativeBrowserState *state, BOOL screenshot, NSError **error);
NSString *ADNativeControlLabel(id title, id description);
NSString *ADNativeHTTPURL(id value);
void ADNativeSummarizeObservation(NSMutableDictionary *result);
NSDictionary *ADNativeAction(ADNativeBrowserState *state, NSDictionary *params, NSError **error);
NSDictionary *ADNativePermissions(void);
NSDictionary *ADNativeDispatch(ADNativeBrowserState *state, NSDictionary *request, NSError **error);
int ADNativeBrowserMain(void);

// Native tab hit evidence is independently testable without inspecting Chrome or sending input.
BOOL ADNativeTabOverlayMatches(NSDictionary *evidence);
BOOL ADNativeTabOverlayHit(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected, AXUIElementRef hit);
BOOL ADNativeFieldOverlayMatches(NSDictionary *evidence);
BOOL ADNativeFieldOverlayHit(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected, AXUIElementRef hit);

// Attached surfaces are proven by native child identity and exact same-process CoreGraphics windows.
BOOL ADNativeIsDialog(AXUIElementRef element);
BOOL ADNativeRefreshSurfaces(ADNativeBrowserState *state, NSError **error);
NSDictionary *ADNativeSurface(ADNativeBrowserState *state, AXUIElementRef element);
BOOL ADNativeSurfaceValid(ADNativeBrowserState *state, NSDictionary *surface);
BOOL ADNativeSurfaceFocus(ADNativeBrowserState *state, AXUIElementRef focused);
BOOL ADNativeSurfaceHit(ADNativeBrowserState *state, CGPoint point, AXUIElementRef hit, BOOL *remoteSheet);
BOOL ADNativePointInWorkspace(ADNativeBrowserState *state, CGPoint point);
CGRect ADNativeWorkspaceBounds(ADNativeBrowserState *state);

// Window creation/rebinding is backend-only; all later operations remain tied to this identity.
NSRunningApplication *ADNativeDefaultChrome(void);
NSArray<NSDictionary *> *ADNativeChromeWindows(pid_t pid);
BOOL ADNativeAttach(ADNativeBrowserState *state, NSDictionary *params, NSError **error);
NSDictionary *ADNativeSelectExistingWindow(ADNativeBrowserState *state, NSRunningApplication *app);
BOOL ADNativeUseExisting(ADNativeBrowserState *state, NSError **error);
id ADNativeExistingCandidate(pid_t pid, id focused, NSArray *windows);
BOOL ADNativePresentExisting(NSRunningApplication *app, id object);
BOOL ADNativeRaise(ADNativeBrowserState *state, NSError **error);
BOOL ADNativePostKey(ADNativeBrowserState *state, CGKeyCode key, CGEventFlags flags, NSError **error);

// Pure key definitions let tests verify allowed native shortcuts without generating any input.
NSDictionary *ADNativeKeyDefinition(NSString *name);
BOOL ADNativeValidateObservation(NSDictionary *prior, NSDictionary *fresh, NSDictionary *params, NSError **error);
BOOL ADNativeAuthorizeEvent(ADNativeBrowserState *state, NSDictionary *params, NSDictionary *control, NSError **error);
BOOL ADNativeSelectedUploadMatches(ADNativeBrowserState *state, NSDictionary *control, NSDictionary *params);
BOOL ADNativeMove(ADNativeBrowserState *state, NSDictionary *params, NSError **error);

// Capture only the already-authorized window; missing screen-recording permission never opens a prompt.
NSDictionary *ADNativeCaptureWindow(pid_t pid, CGWindowID windowID, CGRect expectedBounds, NSError **error);

// A numeric wrapper is evidence of a visible native control, never permission to set its AX value.
BOOL ADNativeNumericWrapperMatches(NSDictionary *evidence, CGRect nativeBounds, CGRect wrapperBounds, CGRect clip);
NSDictionary *ADNativeNumericWrapper(ADNativeBrowserState *state, id object, CGRect clip);

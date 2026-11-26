#import "native-browser.h"

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

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

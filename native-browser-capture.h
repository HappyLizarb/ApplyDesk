#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#include <sys/types.h>

// Capture only the already-authorized window; missing screen-recording permission never opens a prompt.
NSDictionary *ADNativeCaptureWindow(pid_t pid, CGWindowID windowID, CGRect expectedBounds, NSError **error);

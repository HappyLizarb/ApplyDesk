#import "native-browser-input.h"
#include <unistd.h>

// Movement starts only on a screenshot-grounded empty native title region, never a tab or webpage control.
static BOOL titlePoint(ADNativeBrowserState *state, NSDictionary *params, CGPoint *point, NSError **error) {
    if (!params[@"x"] || !params[@"y"] || [params[@"y"] doubleValue]<0 || [params[@"y"] doubleValue]>35) {
        if (error) *error = ADNativeError(@"Choose an empty native title-bar point from the current screenshot before moving the window.");return NO;
    }
    *point = CGPointMake(state.bounds.origin.x+[params[@"x"] doubleValue],state.bounds.origin.y+[params[@"y"] doubleValue]);
    for (NSDictionary *control in state.observation[@"controls"]) {
        NSDictionary *box = control[@"bounds"];CGRect rect = CGRectMake([box[@"x"] doubleValue],[box[@"y"] doubleValue],[box[@"width"] doubleValue],[box[@"height"] doubleValue]);
        if (CGRectContainsPoint(rect,*point)) { if (error) *error = ADNativeError(@"The proposed title-bar point overlaps a visible control.");return NO; }
    }
    if (!ADNativeHitOwned(state,*point,NULL)) { if (error) *error = ADNativeError(@"The title-bar point is covered or outside the owned window.");return NO; }
    return YES;
}

// Native drag events move the owned window; no accessibility property is ever set.
BOOL ADNativeMove(ADNativeBrowserState *state, NSDictionary *params, NSError **error) {
    NSDictionary *bounds = params[@"bounds"];CGPoint start = CGPointZero;
    if (![bounds isKindOfClass:NSDictionary.class] || !titlePoint(state,params,&start,error)) return NO;
    double x = [bounds[@"x"] doubleValue], y = [bounds[@"y"] doubleValue];
    CGRect target = CGRectMake(x,y,state.bounds.size.width,state.bounds.size.height);
    if (!isfinite(x) || !isfinite(y) || !CGRectContainsRect(CGDisplayBounds(CGMainDisplayID()),target)) {
        if (error) *error = ADNativeError(@"The requested position must keep the owned window on the main display.");return NO;
    }
    CGPoint delta = CGPointMake(x-state.bounds.origin.x,y-state.bounds.origin.y), current = start;
    CGEventRef down = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseDown,start,kCGMouseButtonLeft);
    CGEventSetFlags(down,0);CGEventPost(kCGHIDEventTap,down);CFRelease(down);
    BOOL moved = YES;
    for (NSUInteger step=1; step<=20; step++) {
        if (!ADNativeValidateWindow(state,YES,error)) { moved = NO;break; }
        current = CGPointMake(start.x+delta.x*step/20,start.y+delta.y*step/20);
        CGEventRef event = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseDragged,current,kCGMouseButtonLeft);
        CGEventSetFlags(event,0);
        CGEventPost(kCGHIDEventTap,event);CFRelease(event);usleep(20000);
    }
    CGEventRef up = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseUp,current,kCGMouseButtonLeft);
    CGEventSetFlags(up,0);CGEventPost(kCGHIDEventTap,up);CFRelease(up);
    if (!moved || !ADNativeValidateWindow(state,YES,error)) return NO;
    if (fabs(state.bounds.origin.x-x)>4 || fabs(state.bounds.origin.y-y)>4) { if (error) *error = ADNativeError(@"The window did not reach the requested position.");return NO; }
    return YES;
}

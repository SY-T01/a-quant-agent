/* Native menu-bar UI. Tk owns the main thread and dispatches Cocoa events. */
#import <Cocoa/Cocoa.h>

@interface AQMenuController : NSObject <NSMenuDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSMenu *menu;
@property(nonatomic, strong) NSMenuItem *statusLine;
@property(nonatomic, strong) NSMenuItem *scheduleLine;
@property(nonatomic, strong) NSMenuItem *startItem;
@property(nonatomic, strong) NSMenuItem *stopItem;
@property(nonatomic, strong) NSMenuItem *quitItem;
@property(nonatomic, strong) NSMenuItem *manualItem;
@property(nonatomic, copy) NSString *snapshot;
@property(nonatomic, strong) NSMutableArray<NSNumber *> *pendingActions;
@property(nonatomic, assign) int mode;
@property(nonatomic, assign) BOOL menuOpen;
@property(nonatomic, assign) NSUInteger menuOpenCount;
@property(nonatomic, assign) NSUInteger menuCloseCount;
#ifdef AQUANT_UI_TESTING
@property(nonatomic, assign) int testAction;
@property(nonatomic, assign) BOOL testInFlight;
@property(nonatomic, assign) NSUInteger testSequence;
@property(nonatomic, assign) void (*testCapture)(void);
#endif
- (void)performAction:(NSMenuItem *)sender;
@end

@implementation AQMenuController
- (void)performAction:(NSMenuItem *)sender {
    // Cocoa can enter here while _tkinter holds its Tcl mutex. Never call
    // Python/Tk from this stack, even to schedule root.after: it deadlocks.
    if (sender.enabled) [self.pendingActions addObject:@(sender.tag)];
}
- (void)menuWillOpen:(NSMenu *)menu {
    (void)menu;
    self.menuOpen = YES;
    self.menuOpenCount++;
#ifdef AQUANT_UI_TESTING
    if (self.testAction) {
        int action = self.testAction;
        self.testAction = 0;
        if (self.testCapture) {
            NSTimer *capture = [NSTimer timerWithTimeInterval:0.07 repeats:NO block:^(NSTimer *unused) {
                (void)unused;
                self.testCapture(); // QA reads pixels only, never calls Tk.
            }];
            [[NSRunLoop mainRunLoop] addTimer:capture forMode:NSEventTrackingRunLoopMode];
        }
        NSTimer *timer = [NSTimer timerWithTimeInterval:0.15 repeats:NO block:^(NSTimer *unused) {
            (void)unused;
            NSInteger index = [self.menu indexOfItemWithTag:action];
            if (index >= 0) [self.menu performActionForItemAtIndex:index];
            [self.menu cancelTracking];
        }];
        [[NSRunLoop mainRunLoop] addTimer:timer forMode:NSEventTrackingRunLoopMode];
    }
#endif
}
- (void)menuDidClose:(NSMenu *)menu {
    (void)menu;
    self.menuOpen = NO;
    self.menuCloseCount++;
#ifdef AQUANT_UI_TESTING
    @synchronized(self) { self.testInFlight = NO; }
#endif
}
@end

static NSMenuItem *actionItem(AQMenuController *owner, NSString *title, int tag) {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:@selector(performAction:) keyEquivalent:@""];
    item.target = owner;
    item.tag = tag;
    [owner.menu addItem:item];
    return item;
}

void *aquant_menubar_create(void) {
    if (![NSThread isMainThread] || !NSApp) return NULL;
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    AQMenuController *owner = [[AQMenuController alloc] init];
    owner.pendingActions = [[NSMutableArray alloc] init];
    owner.menu = [[NSMenu alloc] initWithTitle:@"A-Quant"];
    owner.menu.autoenablesItems = NO;
    owner.menu.delegate = owner;
    owner.statusLine = [[NSMenuItem alloc] initWithTitle:@"自动日报：已停止" action:NULL keyEquivalent:@""];
    owner.statusLine.enabled = NO;
    [owner.menu addItem:owner.statusLine];
    owner.scheduleLine = [[NSMenuItem alloc] initWithTitle:@"交易日 14:00（北京时间）" action:NULL keyEquivalent:@""];
    owner.scheduleLine.enabled = NO;
    [owner.menu addItem:owner.scheduleLine];
    [owner.menu addItem:[NSMenuItem separatorItem]];
    actionItem(owner, @"打开主窗口…", 1);
    owner.startItem = actionItem(owner, @"启动自动日报", 2);
    owner.stopItem = actionItem(owner, @"停止自动日报", 3);
    owner.manualItem = actionItem(owner, @"立即生成并发送邮件", 5);
    [owner.menu addItem:[NSMenuItem separatorItem]];
    owner.quitItem = actionItem(owner, @"退出 A-Quant", 4);

    owner.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    if (!owner.statusItem.button) return NULL;
    // A compact, template A/chart mark follows macOS light/dark appearance.
    NSImage *image = [NSImage imageWithSize:NSMakeSize(19, 18) flipped:NO drawingHandler:^BOOL(NSRect rect) {
        (void)rect;
        [[NSColor blackColor] setStroke];
        NSBezierPath *mark = [NSBezierPath bezierPath];
        mark.lineWidth = 1.8;
        mark.lineCapStyle = NSLineCapStyleRound;
        mark.lineJoinStyle = NSLineJoinStyleRound;
        [mark moveToPoint:NSMakePoint(2, 2)];
        [mark lineToPoint:NSMakePoint(8.5, 16)];
        [mark lineToPoint:NSMakePoint(15, 2)];
        [mark moveToPoint:NSMakePoint(5, 8)];
        [mark lineToPoint:NSMakePoint(9, 8)];
        [mark lineToPoint:NSMakePoint(12, 11)];
        [mark lineToPoint:NSMakePoint(17, 11)];
        [mark stroke];
        return YES;
    }];
    image.template = YES;
    owner.statusItem.button.image = image;
    owner.statusItem.button.imagePosition = NSImageLeft;
    owner.statusItem.button.accessibilityLabel = @"A-Quant 量化助手";
    owner.statusItem.menu = owner.menu;
    return (__bridge_retained void *)owner;
}

int aquant_menubar_next_action(void *handle) {
    AQMenuController *owner = (__bridge AQMenuController *)handle;
    if (!owner || ![NSThread isMainThread] || owner.menuOpen) return 0;
    while (owner.pendingActions.count) {
        int tag = owner.pendingActions.firstObject.intValue;
        [owner.pendingActions removeObjectAtIndex:0];
        if ([owner.menu itemWithTag:tag].enabled) return tag;
    }
    return 0;
}

void aquant_menubar_update(void *handle, int mode, int canStart, int canStop, int canQuit, int canManual,
                          const char *status, const char *schedule) {
    AQMenuController *owner = (__bridge AQMenuController *)handle;
    if (!owner || ![NSThread isMainThread]) return;
    owner.mode = mode;
    owner.statusLine.title = [NSString stringWithUTF8String:status];
    owner.scheduleLine.title = [NSString stringWithUTF8String:schedule];
    owner.startItem.enabled = canStart;
    owner.stopItem.enabled = canStop;
    owner.quitItem.enabled = canQuit;
    owner.manualItem.enabled = canManual;
    NSColor *color = mode == 1 ? NSColor.systemGreenColor :
                     mode == 2 || mode == 3 ? NSColor.systemOrangeColor : NSColor.secondaryLabelColor;
    owner.statusItem.button.attributedTitle = [[NSAttributedString alloc] initWithString:@" ●"
        attributes:@{NSForegroundColorAttributeName: color,
                     NSFontAttributeName: [NSFont systemFontOfSize:10]}];
    owner.statusItem.button.toolTip = [NSString stringWithFormat:@"A-Quant · %@\n%@",
        owner.statusLine.title, owner.scheduleLine.title];
    // Aqua may change policy when a window is mapped; keep Dock hidden.
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
}

void aquant_menubar_activate(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [NSApp unhide:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

/* Read-only diagnostics, also used by the native UI verification. */
const char *aquant_menubar_snapshot(void *handle) {
    AQMenuController *owner = (__bridge AQMenuController *)handle;
    if (!owner) return NULL;
    NSDictionary *values = @{ @"visible": @(owner.statusItem.visible),
        @"bundle_id": NSBundle.mainBundle.bundleIdentifier ?: @"",
        @"activation_policy": @(NSApp.activationPolicy), @"mode": @(owner.mode),
        @"status": owner.statusLine.title, @"schedule": owner.scheduleLine.title,
        @"can_start": @(owner.startItem.enabled), @"can_stop": @(owner.stopItem.enabled),
        @"menu_open": @(owner.menuOpen), @"menu_open_count": @(owner.menuOpenCount),
        @"menu_close_count": @(owner.menuCloseCount), @"pending_actions": @(owner.pendingActions.count),
        @"can_quit": @(owner.quitItem.enabled) };
    NSData *data = [NSJSONSerialization dataWithJSONObject:values options:0 error:NULL];
    owner.snapshot = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    return owner.snapshot.UTF8String;
}

void aquant_menubar_close(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    AQMenuController *owner = (__bridge_transfer AQMenuController *)handle;
    [[NSStatusBar systemStatusBar] removeStatusItem:owner.statusItem];
    owner.statusItem.menu = nil;
    owner.menu.delegate = nil;
    for (NSMenuItem *item in owner.menu.itemArray) item.target = nil;
}

void aquant_menubar_restore_dock(void) {
    if ([NSThread isMainThread]) [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
}

#ifdef AQUANT_UI_TESTING
// Only compiled into the separate QA component, never the production app.
// A Cocoa timer triggers the real status button while Tcl owns the event loop.
// The old Python callback deadlocks here; direct menu-action tests missed it.
int aquant_menubar_test_click(void *handle, int tag, void (*capture)(void)) {
    AQMenuController *owner = (__bridge AQMenuController *)handle;
    if (!owner || ![NSThread isMainThread] || owner.menuOpen) return 0;
    @synchronized(owner) {
        if (owner.testInFlight) return 0;
        owner.testInFlight = YES;
        owner.testSequence++;
    }
    owner.testAction = tag;
    owner.testCapture = capture;
    NSUInteger sequence = owner.testSequence;
    [NSTimer scheduledTimerWithTimeInterval:0.05 repeats:NO block:^(NSTimer *unused) {
        (void)unused;
        [owner.statusItem.button performClick:nil];
    }];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC), dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        BOOL timedOut;
        @synchronized(owner) { timedOut = owner.testInFlight && owner.testSequence == sequence; }
        if (timedOut) {
            fprintf(stderr, "Native status-button tracking timed out\n");
            _Exit(124); // Only the disposable QA app, including on Tcl deadlock.
        }
    });
    return 1;
}
#endif

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <vector>

using XrSession = int;
using XrResult = int;
using XrPath = int;
using XrAction = int;
using ovrResult = int;
constexpr XrPath XR_NULL_PATH = 0;
constexpr ovrResult ovrSuccess = 0;
#define XR_SUCCEEDED(result) ((result) >= 0)
#define XR_FAILED(result) ((result) < 0)
#define XR_TYPE(type) {}
#define assertmsg(expression, message) assert(expression)
XrResult g_LastResult = 0;
void TraceXrResult(const char*, XrResult) {}
ovrResult ResultToOvrResult(XrResult result) { return result; }
// PRODUCTION_CHECK_XR

enum ovrHandType { ovrHand_Left, ovrHand_Right, ovrHand_Count };
enum ovrControllerType {
    ovrControllerType_LTouch = 1, ovrControllerType_RTouch = 2,
    ovrControllerType_Touch = 3, ovrControllerType_Active = 0xff
};
enum {
    ovrButton_A = 1, ovrButton_B = 2, ovrButton_RThumb = 4,
    ovrButton_X = 0x100, ovrButton_Y = 0x200,
    ovrButton_Enter = 0x100000, ovrButton_Home = 0x1000000,
    ovrTouch_A = 1, ovrTouch_B = 2, ovrTouch_RThumb = 4,
    ovrTouch_RThumbRest = 8, ovrTouch_RIndexTrigger = 0x10,
    ovrTouch_RIndexPointing = 0x20, ovrTouch_RThumbUp = 0x40
};
struct ovrVector2f { float x, y; };
struct ovrInputState {
    unsigned int Buttons, Touches;
    ovrVector2f Thumbstick[2], ThumbstickNoDeadzone[2], ThumbstickRaw[2];
    float IndexTrigger[2], IndexTriggerNoDeadzone[2], IndexTriggerRaw[2];
    float HandTrigger[2], HandTriggerNoDeadzone[2], HandTriggerRaw[2];
    double TimeInSeconds;
    ovrControllerType ControllerType;
};
struct Session { XrSession Session; };
using ovrSession = Session*;
double ovr_GetTimeInSeconds() { return 1.0; }

struct XrActionStateGetInfo { XrAction action; XrPath subactionPath; };
struct XrActionStateBoolean { bool currentState; bool isActive; };
enum ActionId { Enter, Home, AX, BY, Thumb, TouchAX, TouchBY,
    TouchThumb, TouchRest, TouchTrigger, Stick, Index, Grip, Trackpad, ActionCount };
struct FakeState { bool pressed = false; bool active = true; XrResult result = 0; };
FakeState states[ActionCount][2];
XrResult syncResult = 0;
int booleanQueries = 0;
float trackpadY = 0;
XrResult xrGetActionStateBoolean(XrSession, const XrActionStateGetInfo* info,
    XrActionStateBoolean* data)
{
    ++booleanQueries;
    const int hand = info->subactionPath == 2 ? 1 : 0;
    const auto& state = states[info->action][hand];
    // Deliberately leave a true value even on inactive/failed reads. Production
    // code must check validity rather than trusting runtime output on failure.
    data->currentState = state.pressed;
    data->isActive = state.active;
    return state.result;
}

class Runtime {
public:
    enum Hack { HACK_WMR_PROFILE };
    bool wmr = false;
    static Runtime& Get() { static Runtime instance; return instance; }
    bool UseHack(Hack) const { return wmr; }
};

class InputManager {
public:
    static XrPath s_SubActionPaths[ovrHand_Count];
    class Action {
    public:
        Action(int action, bool handed = true) : m_IsHanded(handed), m_Action(action) {}
        bool GetDigital(XrSession session, ovrHandType hand = ovrHand_Left) const;
        float GetAnalog(XrSession, ovrHandType) const {
            return m_Action == Trackpad ? trackpadY : 0;
        }
        ovrVector2f GetVector(XrSession, ovrHandType) const { return {}; }
    private:
        bool m_IsHanded;
        XrAction m_Action;
    };
    class InputDevice {
    public:
        virtual ~InputDevice() = default;
        virtual void GetInputState(XrSession, ovrControllerType, ovrInputState*) = 0;
        ovrControllerType GetType() const { return ovrControllerType_Touch; }
        bool IsConnected() const { return true; }
        static ovrVector2f ApplyDeadzone(ovrVector2f value, float) { return value; }
    };
    class OculusTouch : public InputDevice {
    public:
        void GetInputState(XrSession, ovrControllerType, ovrInputState*) override;
        bool UsesTrackpadButtons(ovrHandType hand) const;
        bool m_WmrBound[ovrHand_Count] = {};
        Action m_Button_Enter{Enter, false}, m_Button_Home{Home, false};
        Action m_Button_AX{AX}, m_Button_BY{BY}, m_Button_Thumb{Thumb};
        Action m_Touch_AX{TouchAX}, m_Touch_BY{TouchBY}, m_Touch_Thumb{TouchThumb};
        Action m_Touch_ThumbRest{TouchRest}, m_Touch_IndexTrigger{TouchTrigger};
        Action m_Thumbstick{Stick}, m_IndexTrigger{Index}, m_HandTrigger{Grip};
        Action m_Trackpad_Buttons{Trackpad};
    };
    std::mutex m_ActionMutex;
    std::vector<InputDevice*> m_InputDevices;
    XrResult SyncActions(XrSession) const { return syncResult; }
    ovrResult GetInputState(ovrSession, ovrControllerType, ovrInputState*);
};
XrPath InputManager::s_SubActionPaths[ovrHand_Count] = {1, 2};
// PRODUCTION_INPUT_METHODS

void check(bool condition, const char* message)
{
    if (!condition) {
        std::fprintf(stderr, "%s\n", message);
        std::exit(1);
    }
}
void reset()
{
    for (auto& action : states)
        for (auto& hand : action)
            hand = {};
    Runtime::Get().wmr = false;
    trackpadY = 0;
    syncResult = 0;
    booleanQueries = 0;
}

int main()
{
    InputManager manager;
    InputManager::OculusTouch touch;
    manager.m_InputDevices.push_back(&touch);
    Session session{1};
    ovrInputState input{};
    auto sample = [&](ovrControllerType type = ovrControllerType_Touch) {
        check(manager.GetInputState(&session, type, &input) == ovrSuccess, "input query failed");
        return input.Buttons;
    };

    reset();
    check(sample() == 0, "idle must not press Menu");
    states[AX][0].pressed = true;
    check(sample() == ovrButton_X, "X alone must remain X");
    states[AX][0].pressed = false;
    states[BY][0].pressed = true;
    check(sample() == ovrButton_Y, "Y alone must remain Y");
    states[AX][0].pressed = true;
    const unsigned chord = ovrButton_X | ovrButton_Y | ovrButton_Enter;
    check(sample() == chord, "left X+Y must also press Menu");
    check(sample() == chord, "held chord must retain ordinary button state");
    check(sample(ovrControllerType_LTouch) == chord, "left-only query must receive chord");
    check(sample(ovrControllerType_Active) == chord, "active-controller query must receive chord");
    check(!(sample(ovrControllerType_RTouch) & ovrButton_Enter), "right-only query must not receive left chord");
    states[AX][0].pressed = false;
    check(sample() == ovrButton_Y, "releasing X must release Menu");
    states[AX][0].pressed = true;
    check(sample() == chord, "chord must work again after release");
    states[BY][0].pressed = false;
    check(sample() == ovrButton_X, "releasing Y must release Menu");

    reset();
    states[AX][1].pressed = states[BY][1].pressed = true;
    check(sample() == (ovrButton_A | ovrButton_B), "right A+B must not press Menu");
    states[AX][1].pressed = false;
    states[AX][0].pressed = true;
    check(!(sample() & ovrButton_Enter), "cross-hand buttons must not press Menu");

    reset();
    states[Enter][0].pressed = true;
    check(sample() == ovrButton_Enter, "native Menu must remain functional");
    states[AX][0].pressed = states[BY][0].pressed = true;
    check(sample() == chord, "native Menu and chord must coexist");
    states[BY][0].pressed = false;
    check(sample() == (ovrButton_X | ovrButton_Enter), "releasing chord must not suppress native Menu");
    states[Enter][0].pressed = false;
    check(sample() == ovrButton_X, "native Menu release must not latch");
    states[Home][0].pressed = true;
    check(sample() == (ovrButton_X | ovrButton_Home), "Home must remain independent");

    reset();
    states[AX][0].pressed = states[BY][0].pressed = true;
    for (int action : {AX, BY}) {
        states[action][0].active = false;
        check(!(sample() & ovrButton_Enter), "inactive chord component must not press Menu");
        states[action][0].active = true;
    }
    states[Enter][0] = {true, false, 0};
    states[AX][0].pressed = false;
    check(!(sample() & ovrButton_Enter), "inactive native Menu must be ignored");

    reset();
    syncResult = 8; // XR_SESSION_NOT_FOCUSED is a nonnegative success result.
    states[AX][0] = states[BY][0] = {true, false, 0};
    check(sample() == 0, "unfocused inactive actions must not press buttons");
    syncResult = 0;
    states[AX][0] = states[BY][0] = {true, true, 3}; // XR_SESSION_LOSS_PENDING.
    check(sample() == chord, "successful nonzero action results remain valid");
    syncResult = 3;
    check(sample() == chord, "successful nonzero sync results remain valid");

    reset();
    Runtime::Get().wmr = true;
    states[AX][0].pressed = states[BY][0].pressed = true;
    check(sample() == ovrButton_X, "WMR lower trackpad click is one button");
    trackpadY = 1;
    check(sample() == ovrButton_Y, "WMR upper trackpad click is one button");

    // Monado binds the WMR profile without being the WMR runtime: its shared
    // trackpad click must still be one button, never the X+Y Menu chord.
    reset();
    touch.m_WmrBound[ovrHand_Left] = true;
    states[AX][0].pressed = states[BY][0].pressed = true;
    check(sample() == ovrButton_X, "bound WMR lower trackpad click is one button");
    trackpadY = 1;
    check(sample() == ovrButton_Y, "bound WMR upper trackpad click is one button");
    touch.m_WmrBound[ovrHand_Left] = false;
    touch.m_WmrBound[ovrHand_Right] = true;
    trackpadY = 0;
    check(sample() == chord, "a WMR right hand must not change the left hand");
    touch.m_WmrBound[ovrHand_Right] = false;

#ifdef NDEBUG
    reset();
    states[AX][0].pressed = states[BY][0].pressed = true;
    for (int action : {AX, BY}) {
        states[action][0].result = -1;
        check(!(sample() & ovrButton_Enter), "failed chord read must not press Menu");
        states[action][0].result = 0;
    }
    states[Enter][0] = {true, true, -1};
    states[AX][0].pressed = false;
    check(!(sample() & ovrButton_Enter), "failed native Menu read must be ignored");
    states[AX][0].pressed = true;
    states[Enter][0].result = 0;
    syncResult = -2;
    booleanQueries = 0;
    check(manager.GetInputState(&session, ovrControllerType_Touch, &input) == -2,
        "failed sync must propagate an error");
    check(input.Buttons == 0 && input.Touches == 0, "failed sync must clear prior input");
    check(booleanQueries == 0, "failed sync must not query stale actions");
#endif
}

"""Gesture vocabulary and label mapping.

Vocabulary justification (see docs/adr/ADR-006-gesture-vocabulary.md)
--------------------------------------------------------------------
The vocabulary must satisfy five constraints simultaneously:

1. **Measurable by a wrist IMU.** Wrist-worn accelerometer+gyroscope can
   reliably separate gross arm motions; it cannot separate finger-level shapes.
2. **Sufficient public training data.** `InGesture` (CC BY 4.0,
   DOI 10.17632/fdxst56tcj.5) provides 8 classes at 200 Hz from 50 participants
   on a wrist sensor — the closest public match to the Modulino Movement
   placement.
3. **Physically reproducible**, so the custom IntentLock dataset can be
   collected with the Modulino.
4. **Pairwise distinguishable** by orientation and direction, since wrist IMU
   distinguishes direction far better than shape.
5. **Safe to perform repeatedly** — no gestures requiring equipment or strain.

The resulting command vocabulary is direction-based, which is the property the
sensor actually measures well:

    LEFT / RIGHT / UP / DOWN  from the Modulino (direction of arm sweep)
    CONFIRM                   a distinct nod-like double pulse
    CANCEL                    a reverse sweep (opposite sign of LEFT)
    NO_GESTURE                a valid class: nothing happened
    UNKNOWN                   a valid class: signal is not confidently any of the above

`ROTATE` from the original specification is deliberately **not** included: a
pure wrist rotation is easily confused with ordinary hand movement and the
InGesture classes provide no matching data to pretrain on. `UNKNOWN` covers it.
This is a documented deviation from the specification, with the reason recorded.
"""

from __future__ import annotations

from enum import Enum


class Gesture(str, Enum):
    """The command-oriented gesture vocabulary.

    `NO_GESTURE` and `UNKNOWN` are first-class outputs, not error states: the
    system must be able to say "nothing happened" and "I do not know".
    """

    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UP = "UP"
    DOWN = "DOWN"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    NO_GESTURE = "NO_GESTURE"
    UNKNOWN = "UNKNOWN"


#: Gestures that can be a command. `NO_GESTURE`/`UNKNOWN` never execute anything.
COMMAND_GESTURES: frozenset[Gesture] = frozenset(
    {
        Gesture.LEFT,
        Gesture.RIGHT,
        Gesture.UP,
        Gesture.DOWN,
        Gesture.CONFIRM,
        Gesture.CANCEL,
    }
)

#: Gestures that are never accepted as the command gesture, but are valid model outputs.
NON_COMMAND_GESTURES: frozenset[Gesture] = frozenset({Gesture.NO_GESTURE, Gesture.UNKNOWN})

#: The ordered class list every gesture model must output. Index order is the
#: model's contract; changing it invalidates exported models, which is why it is
#: a single named constant rather than repeated literals.
GESTURE_CLASSES: tuple[str, ...] = (
    Gesture.LEFT.value,
    Gesture.RIGHT.value,
    Gesture.UP.value,
    Gesture.DOWN.value,
    Gesture.CONFIRM.value,
    Gesture.CANCEL.value,
    Gesture.NO_GESTURE.value,
    Gesture.UNKNOWN.value,
)

#: Default command emitted for each gesture. This is the *only* place a gesture is
#: mapped to an action; the executor validates against its own allowlist too.
GESTURE_TO_COMMAND: dict[str, str] = {
    Gesture.LEFT.value: "PREVIOUS",
    Gesture.RIGHT.value: "NEXT",
    Gesture.UP.value: "VOLUME_UP",
    Gesture.DOWN.value: "VOLUME_DOWN",
    Gesture.CONFIRM.value: "CONFIRM",
    Gesture.CANCEL.value: "CANCEL",
    Gesture.NO_GESTURE.value: "NONE",
    Gesture.UNKNOWN.value: "NONE",
}


def is_command_gesture(label: str | None) -> bool:
    """Whether `label` names a gesture that may become a command."""
    if label is None:
        return False
    try:
        return Gesture(label) in COMMAND_GESTURES
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# InGesture (public pretraining dataset) -> IntentLock vocabulary mapping.
#
# The public dataset's classes are daily-activity gestures, not commands. Only
# the ones with a plausible directional/kinematic analogue are mapped; the rest
# are mapped to UNKNOWN so they act as in-distribution hard negatives for the
# "this is not a command gesture" decision rather than being discarded.
# ---------------------------------------------------------------------------

INGESTURE_TO_INTENTLOCK: dict[str, str] = {
    # 1: Fluid Intake -> a distinct upward wrist motion: usable as UP.
    "fluid_intake": Gesture.UP.value,
    # 2: Answering Phone -> arm raised to head: mapped to UP (kinematically close).
    "answering_phone": Gesture.UP.value,
    # 3: Scratching Head -> up-and-down; deliberately UNKNOWN (not a command).
    "scratching_head": Gesture.UNKNOWN.value,
    # 4: Passing Hand over Face -> lateral sweep: NO_GESTURE (everyday motion).
    "passing_hand_over_face": Gesture.NO_GESTURE.value,
    # 5: Adjusting Glasses -> small wrist rotation: NO_GESTURE.
    "adjusting_glasses": Gesture.NO_GESTURE.value,
    # 6: Holding Chin -> static pose: NO_GESTURE.
    "holding_chin": Gesture.NO_GESTURE.value,
    # 7: Stretching with Hands behind Neck -> large motion: UNKNOWN (not a command).
    "stretching_behind_neck": Gesture.UNKNOWN.value,
    # 0: Free Condition / Other -> NO_GESTURE.
    "free_condition": Gesture.NO_GESTURE.value,
    "other": Gesture.NO_GESTURE.value,
}

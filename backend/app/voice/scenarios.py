"""Emergency scenario catalogue — the agent's reasoning reference.

This is a machine-readable catalogue of scenario *families* with their red flags,
the single most important immediate action, hard do-NOT rules, and the
prioritised questions. It is NOT a keyword matcher the runtime dispatches on: the
LLM reasons over free-form speech. The catalogue exists to

  * document the breadth the agent is expected to cover,
  * seed the scenario-coverage + safety-invariant TESTS (so a regression in the
    prompt that drops, say, the snakebite do-NOT rules is caught automatically),
  * provide a compact "high-risk quick reference" that IS injected into the
    prompt for the few life-threatening cases where speed + exact first aid
    matter most.

Content is conservative, widely-accepted first aid (aligned with WHO / Indian Red
Cross / AHA-style basic guidance). It deliberately contains NO changing facts
(no weather, no "which hospital is open", no live helpline *status*) — those are
retrieved at runtime, never hard-coded here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    title: str
    triggers: tuple[str, ...]          # example phrasings (docs/tests only)
    red_flags: tuple[str, ...]         # signs of immediate life threat
    immediate_action: str              # the single first thing to say/do
    do_not: tuple[str, ...] = ()       # hard safety prohibitions
    key_questions: tuple[str, ...] = ()  # prioritised, ask the top one first
    default_severity: str = "HIGH"
    helpline_category: str = "general"  # maps to emergency_reference.CATEGORIES


# Category families (kept aligned with the task's scenario taxonomy).
CATEGORIES = (
    "medical",
    "accident",
    "disaster",
    "safety",
    "ambiguous",
)

SCENARIOS: tuple[Scenario, ...] = (
    # ── Medical ────────────────────────────────────────────────────────────
    Scenario(
        id="cardiac",
        category="medical",
        title="Chest pain / suspected heart attack",
        triggers=("chest pain", "pressure in chest", "pain in left arm", "seene mein dard"),
        red_flags=("crushing/central chest pain", "pain to arm/jaw", "sweating", "breathlessness"),
        immediate_action="Call 108/112 now. Sit the person down, keep them calm and "
        "still; loosen tight clothing.",
        do_not=("do not let them walk around", "do not give food or water",
                "do not give medication unless their own prescribed one and they ask"),
        key_questions=("Are they conscious and breathing?", "Is the chest pain still there?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="stroke",
        category="medical",
        title="Stroke symptoms (FAST)",
        triggers=("face drooping", "slurred speech", "weak arm", "sudden numbness"),
        red_flags=("face droop", "arm weakness", "speech difficulty", "sudden confusion"),
        immediate_action="Call 108/112 now — note the time symptoms started. Lay them "
        "down with head slightly raised.",
        do_not=("do not give food, drink or medicine", "do not let them drive"),
        key_questions=("When did the symptoms start?", "Can they smile, raise both arms, speak clearly?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="breathing",
        category="medical",
        title="Severe breathing difficulty",
        triggers=("can't breathe", "gasping", "blue lips", "saans nahi aa rahi"),
        red_flags=("blue/grey lips", "unable to speak full sentences", "gasping"),
        immediate_action="Call 108/112 now. Sit them upright, loosen clothing, open a "
        "window; if they have an inhaler, help them use it.",
        do_not=("do not lay them flat if that worsens breathing",),
        key_questions=("Can they speak?", "Any known asthma/allergy?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="unconscious",
        category="medical",
        title="Unconscious / unresponsive",
        triggers=("not waking up", "passed out", "unresponsive", "behosh"),
        red_flags=("no response", "not breathing normally"),
        immediate_action="Call 108/112 now. Check breathing; if breathing, place on "
        "their side (recovery position). If NOT breathing normally, start chest "
        "compressions.",
        do_not=("do not put anything in their mouth", "do not shake them hard"),
        key_questions=("Are they breathing normally?", "Did you see them collapse?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="cpr",
        category="medical",
        title="Cardiac arrest / not breathing (CPR)",
        triggers=("no pulse", "not breathing", "heart stopped"),
        red_flags=("unresponsive AND not breathing normally",),
        immediate_action="Call 108/112 now and start CPR: push hard and fast in the "
        "centre of the chest, about 100–120 pushes a minute, let the chest rise "
        "fully between pushes.",
        do_not=("do not stop until help arrives or they recover",
                "do not delay compressions to check pulse repeatedly"),
        key_questions=("Is anyone else there to help / find an AED?",),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="choking",
        category="medical",
        title="Choking",
        triggers=("choking", "something stuck in throat", "can't breathe eating", "gala ruk gaya"),
        red_flags=("cannot cough/speak/breathe", "clutching throat", "turning blue"),
        immediate_action="If they cannot cough or breathe: give up to 5 firm back "
        "blows between the shoulder blades, then 5 abdominal thrusts; repeat.",
        do_not=("do not do blind finger sweeps", "do not do abdominal thrusts on an "
                "infant — use back blows and chest thrusts instead"),
        key_questions=("Can they cough or make any sound?", "Is it an adult, child or baby?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="bleeding",
        category="medical",
        title="Severe bleeding",
        triggers=("bleeding heavily", "won't stop bleeding", "deep cut", "khoon beh raha"),
        red_flags=("spurting blood", "blood soaking through", "signs of shock"),
        immediate_action="Press hard directly on the wound with a clean cloth and keep "
        "pressing; raise the injured part if you can. Call 108/112.",
        do_not=("do not remove a soaked cloth — add more on top",
                "do not apply a tourniquet unless trained and bleeding is life-threatening"),
        key_questions=("Where is the bleeding and how bad?", "Any object stuck in the wound?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="burns",
        category="medical",
        title="Burns",
        triggers=("burn", "scald", "jal gaya", "hot oil"),
        red_flags=("large/deep burn", "face or airway burn", "electrical/chemical burn"),
        immediate_action="Cool the burn under cool (not ice-cold) running water for "
        "about 20 minutes. Remove rings/tight items near the area.",
        do_not=("do not apply ice, toothpaste, butter or oil", "do not burst blisters",
                "do not peel off stuck clothing"),
        key_questions=("How large is the burn and where?", "What caused it?"),
        default_severity="HIGH",
        helpline_category="medical",
    ),
    Scenario(
        id="seizure",
        category="medical",
        title="Seizure",
        triggers=("seizure", "fit", "convulsion", "mirgi"),
        red_flags=("seizure over 5 min", "repeated seizures", "injury", "first-ever seizure"),
        immediate_action="Clear space around them, cushion the head, and time the "
        "seizure. After it stops, turn them on their side.",
        do_not=("do not restrain them", "do not put anything in their mouth"),
        key_questions=("How long has it lasted?", "Known epilepsy?"),
        default_severity="HIGH",
        helpline_category="medical",
    ),
    Scenario(
        id="poisoning",
        category="medical",
        title="Poisoning / overdose",
        triggers=("swallowed poison", "overdose", "ate pesticide", "zahar"),
        red_flags=("drowsiness", "vomiting", "trouble breathing", "seizures"),
        immediate_action="Call 108/112 now. Find out what and how much was taken and "
        "keep the container/label to show responders.",
        do_not=("do not make them vomit unless a professional tells you to",
                "do not give anything to eat or drink unless advised"),
        key_questions=("What was taken, how much, how long ago?", "Are they awake and breathing?"),
        default_severity="CRITICAL",
        helpline_category="poison",
    ),
    Scenario(
        id="allergy",
        category="medical",
        title="Severe allergic reaction (anaphylaxis)",
        triggers=("allergic reaction", "throat swelling", "hives everywhere", "anaphylaxis"),
        red_flags=("swelling of lips/throat", "difficulty breathing", "collapse"),
        immediate_action="If they have an adrenaline auto-injector (EpiPen), help them "
        "use it in the outer thigh now, then call 108/112.",
        do_not=("do not let them stand/walk suddenly",),
        key_questions=("Do they have an EpiPen?", "Can they breathe?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="diabetic",
        category="medical",
        title="Diabetic emergency",
        triggers=("diabetic", "low sugar", "shaky and sweaty", "very thirsty confused"),
        red_flags=("confusion", "unconsciousness", "very drowsy"),
        immediate_action="If awake and able to swallow, give something sugary (juice, "
        "glucose, sugar). If drowsy/unconscious, call 108/112 and place on their side.",
        do_not=("do not give anything by mouth to someone not fully awake",),
        key_questions=("Are they awake and able to swallow?", "Known diabetes?"),
        default_severity="HIGH",
        helpline_category="medical",
    ),
    Scenario(
        id="snakebite",
        category="medical",
        title="Snakebite",
        triggers=("snake bit", "snakebite", "saap ne kaata"),
        red_flags=("spreading swelling", "difficulty breathing", "drooping eyelids", "bleeding gums"),
        immediate_action="Get to a hospital with anti-venom urgently. Keep them calm "
        "and still; keep the bitten limb still and below heart level; remove rings "
        "and tight items; note the time.",
        do_not=("do not cut the wound", "do not suck out venom", "do not apply a tight "
                "tourniquet", "do not try to catch or kill the snake",
                "do not give alcohol or herbal remedies"),
        key_questions=("Where is the bite and when did it happen?", "Any trouble breathing or swallowing?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="dehydration_heat",
        category="medical",
        title="Severe dehydration / heat stroke",
        triggers=("heat stroke", "very dizzy in sun", "no sweat hot skin", "loo lag gayi"),
        red_flags=("hot dry skin", "confusion", "fainting", "no urine"),
        immediate_action="Move to shade/cool place, cool the body with wet cloths and "
        "fanning, and sip water if fully awake. For confusion/fainting call 108/112.",
        do_not=("do not give fluids to someone not fully alert",),
        key_questions=("Are they alert?", "How long in the heat?"),
        default_severity="HIGH",
        helpline_category="medical",
    ),
    Scenario(
        id="childbirth",
        category="medical",
        title="Childbirth / labour",
        triggers=("baby coming", "in labour", "water broke", "contractions close"),
        red_flags=("urge to push", "crowning", "heavy bleeding", "cord or limb first"),
        immediate_action="Call 102/108/112 now. Have the mother lie down in a clean, "
        "warm place; do not rush delivery — support the baby's head if it emerges.",
        do_not=("do not pull the baby", "do not pull the cord"),
        key_questions=("How far apart are contractions?", "Any bleeding?"),
        default_severity="HIGH",
        helpline_category="pregnancy",
    ),
    Scenario(
        id="mental_health",
        category="medical",
        title="Mental-health crisis / self-harm risk",
        triggers=("want to end my life", "self harm", "panic attack", "can't go on"),
        red_flags=("suicidal intent/plan", "means available", "has harmed self"),
        immediate_action="Stay with me — you are not alone and I'm listening. If you "
        "are in immediate danger, call 112 or Tele-MANAS 14416; is someone nearby "
        "who can be with you?",
        do_not=("do not judge or dismiss", "do not leave an at-risk person alone",
                "do not promise confidentiality that risks their safety"),
        key_questions=("Are you safe right now?", "Do you have a way to reach someone you trust?"),
        default_severity="HIGH",
        helpline_category="mental_health",
    ),
    # ── Accidents / physical danger ─────────────────────────────────────────
    Scenario(
        id="road_accident",
        category="accident",
        title="Road accident",
        triggers=("car crash", "bike accident", "hit by vehicle", "accident ho gaya"),
        red_flags=("trapped person", "heavy bleeding", "unconscious", "on a busy road"),
        immediate_action="Make the scene safe — switch on hazard lights, keep clear of "
        "traffic. Call 108/112. Do not move the injured unless there is fire/danger.",
        do_not=("do not move someone with a possible neck/back injury unless they are "
                "in immediate danger", "do not remove a motorcycle helmet unless airway blocked"),
        key_questions=("Is anyone trapped or unconscious?", "Is the scene safe from traffic?"),
        default_severity="CRITICAL",
        helpline_category="accident",
    ),
    Scenario(
        id="fire",
        category="accident",
        title="Fire / smoke",
        triggers=("fire", "building on fire", "smoke everywhere", "aag lag gayi"),
        red_flags=("trapped", "smoke inhalation", "spreading fire"),
        immediate_action="Get out now and stay out — call 101/112. Keep low under "
        "smoke; feel doors before opening; if clothes catch fire, stop-drop-roll.",
        do_not=("do not go back inside for belongings", "do not use lifts/elevators"),
        key_questions=("Is everyone out?", "Anyone trapped inside?"),
        default_severity="CRITICAL",
        helpline_category="fire",
    ),
    Scenario(
        id="gas_leak",
        category="accident",
        title="Gas leak",
        triggers=("gas leak", "smell of gas", "LPG leaking", "gas ki badboo"),
        red_flags=("strong smell", "dizziness", "many people affected"),
        immediate_action="Get everyone out to fresh air now. Do NOT touch any electrical "
        "switches; open doors/windows from outside if safe. Call 112/101 from outside.",
        do_not=("do not switch lights/appliances on or off", "do not light a flame",
                "do not use a phone next to the leak"),
        key_questions=("Is everyone out and in fresh air?", "Can the supply be shut off safely?"),
        default_severity="CRITICAL",
        helpline_category="fire",
    ),
    Scenario(
        id="electrical",
        category="accident",
        title="Electric shock",
        triggers=("electric shock", "electrocuted", "live wire", "current lag gaya"),
        red_flags=("still in contact with current", "unconscious", "burns"),
        immediate_action="Do NOT touch the person while they may still be in contact. "
        "Switch off the power at the mains first; then call 108/112.",
        do_not=("do not touch them with bare hands if still connected",
                "do not use anything wet/metal to separate them"),
        key_questions=("Is the power switched off?", "Are they conscious and breathing?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="drowning",
        category="accident",
        title="Drowning",
        triggers=("drowning", "pulled from water", "not breathing after water", "doob gaya"),
        red_flags=("not breathing", "unconscious", "blue lips"),
        immediate_action="Get them out of the water safely without endangering yourself. "
        "If not breathing, start CPR and call 108/112.",
        do_not=("do not enter dangerous water to rescue if untrained — reach or throw, don't go",),
        key_questions=("Are they breathing?", "How long were they under water?"),
        default_severity="CRITICAL",
        helpline_category="medical",
    ),
    Scenario(
        id="fall_fracture",
        category="accident",
        title="Fall / suspected fracture",
        triggers=("fell down", "broken bone", "can't move leg", "gir gaya"),
        red_flags=("bone through skin", "head/neck/back injury", "severe deformity"),
        immediate_action="Keep them still and support the injured part where it lies. "
        "For a bad fall or head/neck injury, call 108/112 and do not move them.",
        do_not=("do not try to straighten a deformed limb", "do not move a suspected "
                "spinal injury unless in danger"),
        key_questions=("Did they hit their head/neck/back?", "Can they feel and move fingers/toes?"),
        default_severity="HIGH",
        helpline_category="accident",
    ),
    Scenario(
        id="trapped_collapse",
        category="accident",
        title="Building collapse / trapped person",
        triggers=("trapped", "building collapsed", "stuck under", "malba"),
        red_flags=("difficulty breathing", "crush injury", "unstable structure"),
        immediate_action="Call 112 now. If you are trapped, cover your mouth from dust, "
        "tap on pipes/walls to signal, and conserve energy; avoid shouting constantly.",
        do_not=("do not enter an unstable structure to rescue", "do not use a flame (gas risk)"),
        key_questions=("Where exactly are you / the trapped person?", "Can you breathe freely?"),
        default_severity="CRITICAL",
        helpline_category="disaster",
    ),
    # ── Natural disasters ────────────────────────────────────────────────────
    Scenario(
        id="flood",
        category="disaster",
        title="Flood",
        triggers=("flood", "water rising", "stuck in flood", "baadh"),
        red_flags=("fast-moving water", "water entering home", "stranded"),
        immediate_action="Move to the highest safe level now and call 112/1070. Avoid "
        "walking or driving through moving water — even shallow water is dangerous.",
        do_not=("do not walk/drive through flowing water", "do not touch electrical "
                "fittings in water", "do not shelter in a basement"),
        key_questions=("Are you on high ground / a safe floor?", "Is the water still rising?"),
        default_severity="CRITICAL",
        helpline_category="disaster",
    ),
    Scenario(
        id="earthquake",
        category="disaster",
        title="Earthquake",
        triggers=("earthquake", "building shaking", "bhukamp"),
        red_flags=("trapped", "injuries", "collapsed structures", "aftershocks"),
        immediate_action="If shaking now: Drop, Cover under sturdy furniture, and Hold "
        "on, away from windows. Once still and safe, move outside to open ground.",
        do_not=("do not run outside during strong shaking", "do not use lifts",
                "do not stand in doorways of modern buildings"),
        key_questions=("Are you or anyone hurt or trapped?", "Are you clear of things that can fall?"),
        default_severity="CRITICAL",
        helpline_category="disaster",
    ),
    Scenario(
        id="cyclone_storm",
        category="disaster",
        title="Cyclone / severe storm",
        triggers=("cyclone", "severe storm", "very strong wind", "toofan"),
        red_flags=("flying debris", "flooding", "evacuation order"),
        immediate_action="Shelter in a strong interior room away from windows; if an "
        "evacuation order has been given, follow it now. Keep phone charged.",
        do_not=("do not go out in the eye/lull of the storm", "do not shelter near windows"),
        key_questions=("Are you in a safe, sturdy shelter?", "Has an evacuation order been issued?"),
        default_severity="HIGH",
        helpline_category="disaster",
    ),
    Scenario(
        id="landslide",
        category="disaster",
        title="Landslide",
        triggers=("landslide", "hillside giving way", "mud slide", "bhuskhalan"),
        red_flags=("ground cracking", "tilting trees/poles", "unusual sounds"),
        immediate_action="Move away from the slope path immediately, to higher stable "
        "ground to the side of the flow. Call 112/1070.",
        do_not=("do not stay in the path of the slide", "do not cross a fresh slide area"),
        key_questions=("Are you clear of the slide path?", "Anyone missing or buried?"),
        default_severity="CRITICAL",
        helpline_category="disaster",
    ),
    # ── Personal safety / rescue ───────────────────────────────────────────
    Scenario(
        id="lost_stranded",
        category="safety",
        title="Lost / stranded / isolated",
        triggers=("i'm lost", "stranded", "no way back", "raasta bhool gaya"),
        red_flags=("night/cold exposure", "no water", "injury", "low battery"),
        immediate_action="Stay where you are if it's safe — it's easier to find you. "
        "Share your location; conserve phone battery and keep warm/shaded.",
        do_not=("do not wander if injured or it's dark/dangerous",),
        key_questions=("What's around you that you can describe?", "How much phone battery is left?"),
        default_severity="MEDIUM",
        helpline_category="general",
    ),
    Scenario(
        id="violence_threat",
        category="safety",
        title="Violence / threat / harassment",
        triggers=("being attacked", "someone is threatening me", "domestic violence", "peecha"),
        red_flags=("weapon", "immediate physical danger", "attacker present"),
        immediate_action="Get to a safe place and call 112/100 now. If you can't speak, "
        "many regions let you signal silently — but getting away is the priority.",
        do_not=("do not confront an armed attacker", "do not stay in the danger zone to gather belongings"),
        key_questions=("Are you safe right now, or is the danger still present?", "Where are you?"),
        default_severity="CRITICAL",
        helpline_category="police",
    ),
    Scenario(
        id="child_vulnerable",
        category="safety",
        title="Child / vulnerable person in danger",
        triggers=("child is missing", "kid in danger", "elderly person fell and alone"),
        red_flags=("child alone/at risk", "abuse", "unable to care for self"),
        immediate_action="Call 112; for a child specifically you can also reach Childline "
        "1098. Keep the child in sight and safe if you are with them.",
        do_not=("do not leave the vulnerable person alone if at risk",),
        key_questions=("Is the person with you and safe now?", "What is the immediate danger?"),
        default_severity="HIGH",
        helpline_category="child",
    ),
    Scenario(
        id="low_resource",
        category="safety",
        title="Low battery / poor signal / limited mobility",
        triggers=("phone about to die", "barely any signal", "can't move", "battery low"),
        red_flags=("about to lose contact", "cannot self-evacuate"),
        immediate_action="Quickly: share your location and the key facts now in case we "
        "get cut off, then call the emergency number. I'll keep replies short.",
        do_not=("do not waste battery on long messages",),
        key_questions=("What is your location?", "What is the single most urgent problem?"),
        default_severity="HIGH",
        helpline_category="general",
    ),
    # ── Ambiguous / non-emergency ───────────────────────────────────────────
    Scenario(
        id="symptom_check",
        category="ambiguous",
        title="Is this urgent? (symptom triage)",
        triggers=("is this serious", "should i go to hospital", "mild chest discomfort",
                  "kya ye serious hai"),
        red_flags=("any red-flag symptom means treat as the matching emergency",),
        immediate_action="Ask the key red-flag questions first; if any red flag is "
        "present, treat it as that emergency now. Otherwise give measured advice and "
        "a clear 'seek care if…' threshold.",
        do_not=("do not give false reassurance when serious symptoms are possible",
                "do not diagnose definitively"),
        key_questions=("What exactly are you feeling, and for how long?",
                       "Any trouble breathing, chest pain, fainting or weakness?"),
        default_severity="UNKNOWN",
        helpline_category="general",
    ),
    Scenario(
        id="first_aid_question",
        category="ambiguous",
        title="General first-aid / preparedness question",
        triggers=("how do i do cpr", "what's in a first aid kit", "how to treat a burn"),
        red_flags=("if it's actually happening now, switch to the live emergency flow",),
        immediate_action="First check if this is happening right now; if yes, switch to "
        "acting. If it's a general question, give a short, correct, practical answer.",
        do_not=("do not recommend medicines or dosages",),
        key_questions=("Is this happening right now, or are you asking for later?",),
        default_severity="LOW",
        helpline_category="general",
    ),
    Scenario(
        id="unclear_report",
        category="ambiguous",
        title="Unclear / incomplete / contradictory report",
        triggers=("garbled speech", "something's wrong but can't say", "conflicting details"),
        red_flags=("caller cannot speak freely", "possible silent duress"),
        immediate_action="Stay calm and ask one simple yes/no question to find the most "
        "urgent fact; assume it could be serious until you know otherwise.",
        do_not=("do not guess details and act on them as if confirmed",),
        key_questions=("Are you safe right now — yes or no?",
                       "Is someone hurt — yes or no?"),
        default_severity="UNKNOWN",
        helpline_category="general",
    ),
    Scenario(
        id="off_topic",
        category="ambiguous",
        title="Off-topic / not an emergency",
        triggers=("what's the weather like", "tell me a joke", "random question"),
        red_flags=("hidden distress behind a casual question",),
        immediate_action="Answer briefly and steer back to safety; stay alert for any "
        "hint of real danger behind the question.",
        do_not=("do not treat every message as an emergency",
                "do not refuse to be helpful"),
        key_questions=("Is everything okay, or is there something you need help with?",),
        default_severity="LOW",
        helpline_category="general",
    ),
)


# High-risk scenarios whose exact first aid must be fast + correct — these get
# summarised into the prompt (behavioural, not changing facts).
_QUICK_REF_IDS = (
    "cpr", "choking", "bleeding", "snakebite", "stroke", "allergy", "childbirth",
)


def quick_reference_block() -> str:
    """Compact, authoritative first-aid reminders for the highest-risk cases.

    Injected into the system prompt. Intentionally terse — the model already
    knows first aid; this pins the *exact* safety-critical do/don'ts so they
    can't drift, and keeps them phrasable in one spoken breath.
    """
    by_id = {s.id: s for s in SCENARIOS}
    lines: list[str] = []
    for sid in _QUICK_REF_IDS:
        s = by_id[sid]
        line = f"- {s.title}: {s.immediate_action}"
        if s.do_not:
            line += " DO NOT: " + "; ".join(s.do_not) + "."
        lines.append(line)
    return "\n".join(lines)


def categories() -> tuple[str, ...]:
    return CATEGORIES


def scenarios_in(category: str) -> list[Scenario]:
    return [s for s in SCENARIOS if s.category == category]

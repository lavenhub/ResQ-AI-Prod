export type VoiceStatus =
  | "idle"
  | "connecting"
  | "listening"   // agent is waiting for / hearing the caller
  | "thinking"    // transcribing + generating a reply
  | "speaking"    // agent is talking
  | "error";

export interface TranscriptEntry {
  id: string;
  sender: "user" | "resq" | "system";
  text: string;
  timestamp: string;
}

export interface BriefingReport {
  incidentType: string;
  victimStatus: {
    conscious: "Yes" | "No" | "Unknown";
    breathing: "Yes" | "No" | "Unknown";
    bleeding: "Yes" | "No" | "Unknown";
  };
  location: string;
  timelineActions: string[];
  arrivingHazards: string;
  urgencyLevel: "CRITICAL" | "HIGH" | "MEDIUM";
}

export const INDIAN_LANGUAGES = [
  "English",
  "Hindi",
  "Bengali",
  "Telugu",
  "Marathi",
  "Tamil",
  "Gujarati",
  "Kannada",
  "Malayalam",
  "Odia",
  "Punjabi",
  "Assamese",
  "Maithili",
  "Santali",
  "Kashmiri",
  "Nepali",
  "Sindhi",
  "Konkani",
  "Dogri",
  "Manipuri",
  "Bodo",
  "Sanskrit",
];

export interface FirstAidGuide {
  id: string;
  title: string;
  iconName: "shield" | "bug" | "heart" | "wind" | "flame" | "user";
  precautions: string[];
  steps: string[];
  warnings: string[];
}

export const GUIDES: FirstAidGuide[] = [
  {
    id: "bleeding",
    title: "Heavy Bleeding",
    iconName: "shield",
    precautions: [
      "Ensure you are safe from hazards before assisting.",
      "Wear gloves if accessible.",
    ],
    steps: [
      "Apply FIRM continuous direct pressure with a clean cloth.",
      "Elevate limb above chest if bleeding doesn't stop.",
      "Keep patient flat and warm.",
    ],
    warnings: [
      "Do NOT remove original cloth — add layers over it.",
      "Do NOT apply tourniquets unless trained.",
    ],
  },
  {
    id: "snakebite",
    title: "Snakebite First Aid",
    iconName: "bug",
    precautions: [
      "Keep victim completely still and calm.",
      "Position bitten limb BELOW heart level.",
    ],
    steps: [
      "Call 108/112 immediately.",
      "Remove rings and tight clothing near bite.",
      "Clean bite with clean water.",
    ],
    warnings: [
      "Do NOT cut, slash or suction the wound.",
      "Do NOT wrap tight tourniquets.",
      "Do NOT give alcohol or pain meds.",
    ],
  },
  {
    id: "cpr",
    title: "CPR (No Breathing / Collapse)",
    iconName: "heart",
    precautions: [
      "Check consciousness — shake and shout 'Are you okay?'",
      "If not breathing normally, CALL 108/112 NOW.",
    ],
    steps: [
      "Place flat on firm surface.",
      "Hands center of chest on breastbone.",
      "Press hard and fast 100-120/min, 2 inches deep — rhythm of 'Staying Alive'.",
    ],
    warnings: [
      "Do NOT stop compressions until responders arrive.",
      "Do NOT press ribs or abdomen.",
    ],
  },
  {
    id: "choking",
    title: "Choking (Blocked Airway)",
    iconName: "wind",
    precautions: ["Ask 'Are you choking?' — if can't speak/breathe, act immediately."],
    steps: [
      "Stand behind, arms around waist, lean forward.",
      "Fist just above navel, grasp with other hand.",
      "Quick sharp upward-inward Heimlich thrusts until cleared.",
    ],
    warnings: [
      "Do NOT do abdominal thrusts on infants or pregnant — use chest thrusts.",
      "Do NOT blindly sweep throat with fingers.",
    ],
  },
  {
    id: "burns",
    title: "Burns & Scalds",
    iconName: "flame",
    precautions: ["Move person away from heat/chemical source immediately."],
    steps: [
      "Cool burn with running tepid water for 15-20 minutes.",
      "Cover loosely with cling wrap or sterile dressing.",
    ],
    warnings: [
      "Do NOT put butter, toothpaste, ice on burn.",
      "Do NOT break blisters or peel skin.",
    ],
  },
  {
    id: "spine",
    title: "Suspected Spine Injury",
    iconName: "user",
    precautions: [
      "Suspect spine injury after fall from height, auto accident, or head trauma.",
    ],
    steps: [
      "Keep completely still. Say 'Try not to move your head.'",
      "Support head neutral position until paramedics arrive.",
    ],
    warnings: [
      "Do NOT move person unless in immediate danger (fire/water/collapse).",
      "Do NOT twist or rotate neck.",
    ],
  },
];

"""
emergency_assistant.py — Safe, immediate, step-by-step first-aid guidance engine.
================================================================================
Designed for patient/bystander assistance after an emergency request is submitted
while the ambulance is actively in-transit.

Guiding Principles:
1. DOES NOT replace emergency doctors, paramedics, or dispatchers.
2. Prioritizes dispatcher instructions and national emergency services (112).
3. Retrieval + Safety Rules + AI response architecture.
4. Deterministic fallback to approved first-aid protocols if AI is unavailable.
5. Strict prompt injection and safety constraint enforcement.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EmergencyAssistantMessage, EmergencyAssistantSession, EmergencyGuidance, EmergencyRequest, EmergencySafetyEvent, User

EMERGENCY_PHONE_NUMBER = os.getenv("EMERGENCY_PHONE_NUMBER", "112")

# ==============================================================================
# EMERGENCY CATEGORIES
# ==============================================================================
CATEGORIES = [
    "pregnancy_emergency",
    "child_emergency",
    "choking",
    "cardiac_emergency",
    "stroke_symptoms",
    "unconsciousness",
    "breathing_difficulty",
    "severe_bleeding",
    "road_accident",
    "burns",
    "seizure",
    "fracture_injury",
    "poisoning",
    "allergic_reaction",
    "general_emergency",
]

# ==============================================================================
# APPROVED EMERGENCY FIRST-AID KNOWLEDGE BASE (WHO / Red Cross / ERC Standards)
# Structured across all 6 Indian languages: en, hi, bn, mr, ta, te
# ==============================================================================
APPROVED_PROTOCOLS: dict[str, dict[str, dict[str, Any]]] = {
    "severe_bleeding": {
        "en": {
            "title": "Severe Bleeding First-Aid",
            "do_this_now": [
                "Apply direct, firm pressure on the wound using a clean cloth, sterile pad, or bare hand if nothing else is available.",
                "Keep continuous pressure without lifting the cloth to check. If blood soaks through, add another cloth on top.",
                "Help the person lie down to reduce the risk of fainting and maintain vital blood flow.",
                "Follow any direct instructions from the emergency dispatcher."
            ],
            "next_action": [
                "Maintain direct pressure until ambulance paramedics arrive.",
                "Keep the person warm by covering them with a blanket or coat.",
                "Reassure the person and keep them calm and still."
            ],
            "what_not_to_do": [
                "DO NOT remove any deeply embedded objects (like glass or knives) from the wound; press around them.",
                "DO NOT remove the soaked cloth—always layer additional cloths over it.",
                "DO NOT apply an improvised tourniquet unless specifically trained and instructed by the dispatcher.",
                "DO NOT give the person food, water, or medication."
            ],
            "when_to_escalate": "If the person becomes pale, cold, drowsy, confused, or unresponsive, call 112 immediately and inform the dispatcher that the patient may be going into shock."
        },
        "hi": {
            "title": "गंभीर रक्तस्राव (तेज़ खून बहना) प्राथमिक उपचार",
            "do_this_now": [
                "घाव पर साफ कपड़े, बाँधने की पट्टी या हाथ से तुरंत सीधा और तेज दबाव बनाएं।",
                "दबाव लगातार बनाए रखें। यदि कपड़ा खून से भीग जाए, तो उसे हटाएं नहीं, उसके ऊपर दूसरा कपड़ा रख दें।",
                "मरीज को तुरंत लिटा दें ताकि बेहोशी का खतरा कम हो सके।",
                "आपातकालीन डिस्पैचर (112) के निर्देशों का पालन करें।"
            ],
            "next_action": [
                "जब तक एम्बुलेंस न पहुंचे, घाव पर लगातार दबाव बनाए रखें।",
                "मरीज को चादर या कंबल से ढककर गर्म रखें।",
                "मरीज को शांत रखें और ज्यादा हिलने-डुलने न दें।"
            ],
            "what_not_to_do": [
                "घाव में धंसी हुई किसी भी वस्तु (जैसे कांच या कील) को बाहर न निकालें; उसके आसपास दबाव बनाएं।",
                "खून से भीगे कपड़े को कभी न हटाएं—उसके ऊपर दूसरा कपड़ा लगाएं।",
                "मरीज को कुछ भी खाने या पीने को न दें।"
            ],
            "when_to_escalate": "यदि मरीज बेहोश होने लगे, शरीर ठंडा पड़ने लगे या सांस लेने में दिक्कत हो, तुरंत 112 पर सूचना दें।"
        },
        "bn": {
            "title": "মারাত্মক রক্তক্ষরণ প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "ক্ষতস্থানের উপর পরিষ্কার কাপড় বা গজ দিয়ে সরাসরি শক্ত করে চাপ দিয়ে ধরে রাখুন।",
                "চাপ দেওয়া বন্ধ করবেন না। কাপড় ভিজে গেলে তা না সরিয়ে উপরে আরেকটি কাপড় দিন।",
                "রোগীকে সোজা শুইয়ে দিন যাতে রক্তচাপ স্বাভাবিক থাকে।",
                "জরুরি ডিসপ্যাচারের নির্দেশাবলী অনুসরণ করুন।"
            ],
            "next_action": [
                "অ্যাম্বুলেন্স পৌঁছানো পর্যন্ত ক্ষতস্থানে অবিরাম চাপ বজায় রাখুন।",
                "রোগীকে চাদর বা কম্বল দিয়ে ঢেকে উষ্ণ রাখুন।",
                "রোগীকে শান্ত রাখুন এবং নড়াচড়া করতে দেবেন না।"
            ],
            "what_not_to_do": [
                "ক্ষতে ঢুকে থাকা কোনো ধারালো বস্তু টেনে বের করবেন না।",
                "রক্তে ভেজা কাপড় কখনোই সরাবেন না।",
                "রোগীকে কোনো খাবার বা জল দেবেন না।"
            ],
            "when_to_escalate": "রোগী নিস্তেজ বা অজ্ঞান হয়ে পড়লে অবিলম্বে ১১২-তে কল করে জানান।"
        },
        "mr": {
            "title": "तीव्र रक्तस्राव प्रथमोपचार",
            "do_this_now": [
                "जखमेवर स्वच्छ कापडाने किंवा हाताने थेट आणि जोरदार दाब द्या.",
                "दाब काढू नका. कापड रक्ताने भिजल्यास ते न काढता त्यावर दुसरे कापड ठेवा.",
                "रुग्णाला ताबडतोब झोपवा जेणेकरून चक्कर येणे टाळता येईल.",
                "आपत्कालीन डिस्पॅचरच्या सूचनांचे पालन करा."
            ],
            "next_action": [
                "अ‍ॅम्ब्युलन्स येईपर्यंत जखमेवर सतत दाब चालू ठेवा.",
                "रुग्णाला चादर किंवा कपड्याने झाकून उबदार ठेवा.",
                "रुग्णाला धीर द्या आणि शांत ठेवा."
            ],
            "what_not_to_do": [
                "जखमेमध्ये रुतलेली कोणतीही वस्तू बाहेर काढू नका.",
                "रक्ताने भिजलेले कापड काढू नका.",
                "रुग्णाला काहीही खाण्यास किंवा पिण्यास देऊ नका."
            ],
            "when_to_escalate": "रुग्ण बेशुद्ध पडल्यास किंवा त्वचा थंड पडल्यास लगेच 112 वर संपर्क साधा."
        },
        "ta": {
            "title": "கடுமையான இரத்தப்போக்கு முதலுதவி",
            "do_this_now": [
                "சுத்தமான துணி அல்லது கைகளால் காயத்தின் மீது நேரடியாக அழுத்தம் கொடுங்கள்.",
                "அழுத்தத்தை தளர்த்த வேண்டாம். துணி நனைந்தால் அதை எடுக்காமல் மேலே மற்றொரு துணியை வைக்கவும்.",
                "பாதிக்கப்பட்டவரை உடனே படுக்க வைக்கவும்.",
                "அவசர சேவை வழிகாட்டுதலைப் பின்பற்றவும்."
            ],
            "next_action": [
                "ஆம்புலன்ஸ் வரும் வரை அழுத்தத்தைத் தொடரவும்.",
                "போர்வையால் போர்த்தி உடலை கதகதப்பாக வைக்கவும்.",
                "பாதிக்கப்பட்டவரை அமைதியாக இருக்கச் செய்யவும்."
            ],
            "what_not_to_do": [
                "காயத்தில் குத்தியுள்ள பொருட்களை வெளியே இழுக்க வேண்டாம்.",
                "இரத்தம் நனைந்த துணியை அகற்ற வேண்டாம்.",
                "உணவு அல்லது தண்ணீர் கொடுக்க வேண்டாம்."
            ],
            "when_to_escalate": "மயக்கம் அடைந்தாலோ அல்லது உடல் குளிர்ந்து போனாலோ உடனடியாக 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "తీవ్ర రక్తస్రావం ప్రథమ చికిత్స",
            "do_this_now": [
                "గాయంపై శుభ్రమైన గుడ్డ లేదా చేతితో నేరుగా గట్టిగా ఒత్తిడి ఉంచండి.",
                "ఒత్తిడిని తీయకండి. గుడ్డ రక్తంతో తడిసిపోతే దాన్ని తీయకుండా పైన మరొక గుడ్డను ఉంచండి.",
                "బాధితుడిని నేలపై పడుకోబెట్టండి.",
                "అత్యవసర విభాగం సూచనలను పాటించండి."
            ],
            "next_action": [
                "అంబులెన్స్ వచ్చే వరకు గాయంపై ఒత్తిడిని కొనసాగించండి.",
                "బాధితుడిని దుప్పటితో కప్పి వెచ్చగా ఉంచండి.",
                "బాధితుడికి ధైర్యం చెప్పి స్థిరంగా ఉంచండి."
            ],
            "what_not_to_do": [
                "గాయంలో దిగిన వస్తువులను బయటకు లాగవద్దు.",
                "రక్తంతో తడిసిన గుడ్డను తొలగించవద్దు.",
                "తిండి లేదా నీరు ఇవ్వవద్దు."
            ],
            "when_to_escalate": "బాధితుడు స్పృహ కోల్పోతే వెంటనే 112 కు సమాచారం అందించండి."
        }
    },
    "unconsciousness": {
        "en": {
            "title": "Unconscious / Unresponsive Person First-Aid",
            "do_this_now": [
                "Gently tap the shoulders and shout: 'Are you okay? Can you hear me?'",
                "Check for normal breathing: Look at the chest for 5 to 10 seconds. (Gasping or infrequent snoring is NOT normal breathing).",
                "IF NOT BREATHING NORMALLY: Place hands in the center of the chest and push hard and fast (100-120 beats per minute) until the ambulance arrives or follow the dispatcher's CPR instructions.",
                "IF BREATHING NORMALLY: Gently roll the person onto their side in the recovery position with their airway open, unless a neck or spinal injury is suspected."
            ],
            "next_action": [
                "Stay beside the person constantly and monitor their breathing continuously.",
                "If an Automated External Defibrillator (AED) is available nearby, turn it on and follow its spoken voice prompts.",
                "Keep their airway open by gently tilting the head back and lifting the chin."
            ],
            "what_not_to_do": [
                "DO NOT give any water, liquids, or medicines to an unconscious person.",
                "DO NOT slap, shake vigorously, or throw water on the face.",
                "DO NOT place a pillow under the head, as it can close the airway.",
                "DO NOT leave the person unattended."
            ],
            "when_to_escalate": "If breathing stops or turns into abnormal gasping, inform the 112 emergency dispatcher immediately and begin continuous chest compressions."
        },
        "hi": {
            "title": "बेहोश या अनुत्तरदायी व्यक्ति का प्राथमिक उपचार",
            "do_this_now": [
                "कंधों को हल्के से थपथपाएं और जोर से पूछें: 'क्या आप ठीक हैं? क्या आप मुझे सुन सकते हैं?'",
                "सांस की जांच करें: 5 से 10 सेकंड तक छाती के उठने-गिरने को देखें (घुरघुराहट या हांफना सामान्य सांस नहीं है)।",
                "यदि सांस नहीं चल रही है: छाती के बीच में दोनों हाथ रखकर तेजी से दबाना (CPR) शुरू करें (100-120 बार प्रति मिनट)।",
                "यदि सांस सामान्य चल रही है: मरीज को करवट दिलाकर (रिकवरी पोजिशन) लिटाएं ताकि सांस की नली खुली रहे।"
            ],
            "next_action": [
                "मरीज के पास लगातार बने रहें और हर मिनट सांस की निगरानी करें।",
                "सिर को हल्का सा पीछे झुकाकर और ठुड्डी उठाकर सांस का रास्ता साफ रखें।"
            ],
            "what_not_to_do": [
                "बेहोश व्यक्ति के मुंह में पानी, दवा या खाना बिल्कुल न डालें।",
                "चेहरे पर पानी न छिड़कें और जोर से न हिलाएं।",
                "सिर के नीचे तकिया न लगाएं, इससे सांस की नली बंद हो सकती है।"
            ],
            "when_to_escalate": "यदि सांस रुक जाए तो तुरंत 112 पर बताएं और छाती पर दबाव (CPR) जारी रखें।"
        },
        "bn": {
            "title": "অচেতন ব্যক্তির প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "কাঁধে মৃদু চাপ দিয়ে জোরে ডাকুন: 'আপনি কি শুনতে পাচ্ছেন?'",
                "শ্বাসপ্রশ্বাস লক্ষ্য করুন: ৫-১০ সেকেন্ড দেখুন বুক উঠছে কি না।",
                "শ্বাস না থাকলে: বুকের মাঝখানে দুই হাত রেখে দ্রুত চাপ (CPR) দিতে থাকুন (প্রতি মিনিটে ১০০-১২০ বার)।",
                "শ্বাস থাকলে: রোগীকে আলতো করে একপাশে কাত করে শুইয়ে দিন যাতে শ্বাসনালী পরিষ্কার থাকে।"
            ],
            "next_action": [
                "রোগীর পাশে থাকুন এবং অনবরত শ্বাস পর্যবেক্ষণ করুন।",
                "মাথা সামান্য পেছনের দিকে হেলিয়ে চিবুক তুলে রাখুন।"
            ],
            "what_not_to_do": [
                "অচেতন রোগীকে জল বা ওষুধ খাওয়ানোর চেষ্টা করবেন না।",
                "মুখে জল ছিটাবেন না বা জোরে ঝাঁকাবেন না।",
                "মাথার নিচে বালিশ দেবেন না।"
            ],
            "when_to_escalate": "শ্বাস বন্ধ হয়ে গেলে সাথে সাথে ১১২-তে জানান এবং সিপিআর চালিয়ে যান।"
        },
        "mr": {
            "title": "बेशुद्ध व्यक्तीसाठी प्रथमोपचार",
            "do_this_now": [
                "खांद्यावर हलके थोपटून विचारा: 'तुम्ही ठीक आहात का?'",
                "श्वासोच्छ्वास तपासा: छातीची हालचाल ५ ते १० सेकंद पहा.",
                "श्वास नसेल तर: छातीच्या मध्यभागी दोन्ही हातांनी वेगाने दाब (CPR) देणे सुरू करा (१००-१२० प्रति मिनिट).",
                "श्वास चालू असल्यास: रुग्णाला एका कुशीवर वळवून झोपवा."
            ],
            "next_action": [
                "रुग्णाच्या सतत जवळ राहा आणि श्वासावर लक्ष ठेवा.",
                "श्वासनलिका मोकळी ठेवण्यासाठी हनुवटी थोडी वर उचला."
            ],
            "what_not_to_do": [
                "बेशुद्ध व्यक्तीला पाणी किंवा औषध पाजू नका.",
                "तोंडावर पाणी मारू नका आणि हलवू नका.",
                "डोक्याखाली उशी ठेवू नका."
            ],
            "when_to_escalate": "श्वास थांबल्यास त्वरित 112 वर सांगा आणि सीपीआर सुरू ठेवा."
        },
        "ta": {
            "title": "மயக்கமடைந்த நபருக்கான முதலுதவி",
            "do_this_now": [
                "தோள்களைத் தட்டி 'நீங்கள் நலமாக இருக்கிறீர்களா?' என்று சத்தமாகக் கேளுங்கள்.",
                "சுவாசத்தைச் சரிபார்க்கவும்: 5-10 வினாடிகள் மார்பு உயர்வதைக் கவனிக்கவும்.",
                "சுவாசம் இல்லையெனில்: மார்பின் மையத்தில் கைகளை வைத்து வேகமாக அழுத்தவும் (CPR - நிமிடத்திற்கு 100-120 முறை).",
                "சுவாசம் இருந்தால்: நபரை ஒரு பக்கமாக ஒருக்களித்துப் படுக்க வைக்கவும்."
            ],
            "next_action": [
                "அருகிலேயே இருந்து சுவாசத்தைத் தொடர்ந்து கவனிக்கவும்.",
                "வாய்ப் பகுதியைத் திறந்து சுவாசப் பாதையைச் சீராக வைக்கவும்."
            ],
            "what_not_to_do": [
                "மயக்கத்தில் உள்ளவருக்குத் தண்ணீர் அல்லது உணவு கொடுக்கக் கூடாது.",
                "முகத்தில் தண்ணீர் தெளிக்கவோ உலுக்கவோ கூடாது.",
                "தலைக்குத் தலையணை வைக்க வேண்டாம்."
            ],
            "when_to_escalate": "சுவாசம் நின்றால் உடனே 112 ஐ அழைத்து CPR ஐத் தொடரவும்."
        },
        "te": {
            "title": "స్పృహలేని వ్యక్తికి ప్రథమ చికిత్స",
            "do_this_now": [
                "భుజాలను తట్టి 'మీరు వింటున్నారా?' అని గట్టిగా అడగండి.",
                "శ్వాసను పరిశీలించండి: 5-10 సెకన్ల పాటు ఛాతీ కదలికను చూడండి.",
                "శ్వాస లేకపోతే: ఛాతీ మధ్యలో రెండు చేతులతో వేగంగా ఒత్తడం (CPR) ప్రారంభించండి (నిమిషానికి 100-120 సార్లు).",
                "శ్వాస ఉంటే: బాధితుడిని ఒక పక్కకు తిప్పి పడుకోబెట్టండి."
            ],
            "next_action": [
                "ఎల్లప్పుడూ దగ్గరే ఉండి శ్వాసను గమనిస్తూ ఉండండి.",
                "దవడను పైకి ఎత్తి శ్వాస నాళం తెరిచి ఉంచండి."
            ],
            "what_not_to_do": [
                "స్పృహ లేని వారికి నీరు లేదా మందులు ఇవ్వవద్దు.",
                "ముఖంపై నీళ్లు చల్లడం లేదా ఊపడం చేయవద్దు.",
                "తల కింద దిండు పెట్టవద్దు."
            ],
            "when_to_escalate": "శ్వాస ఆగిపోతే వెంటనే 112 కు కాల్ చేసి CPR కొనసాగించండి."
        }
    },
    "choking": {
        "en": {
            "title": "Choking First-Aid (Adult / Child / Infant)",
            "do_this_now": [
                "Check whether they can speak, cough forcefully, or breathe. If they can cough forcefully, encourage them to keep coughing.",
                "FOR CONSCIOUS ADULT OR CHILD OVER 1 YEAR: Stand behind them, lean them forward, give 5 firm back blows between the shoulder blades with the heel of your hand.",
                "If back blows do not dislodge the object: Give 5 quick abdominal thrusts (Heimlich maneuver) by placing a fist above their navel and pulling inward and upward.",
                "FOR INFANT UNDER 1 YEAR: Lay the infant face down along your forearm supporting their head, give 5 gentle back blows, then turn face up and give 5 chest thrusts with 2 fingers. NEVER perform abdominal thrusts on an infant."
            ],
            "next_action": [
                "Alternate between 5 back blows and 5 thrusts until the object clears or emergency help arrives.",
                "If the person becomes unresponsive: Lower them gently to the floor, call 112 immediately, and begin CPR chest compressions."
            ],
            "what_not_to_do": [
                "DO NOT perform blind finger sweeps in the mouth (you could push the object deeper).",
                "DO NOT perform abdominal thrusts on infants under 1 year of age.",
                "DO NOT offer water or food while choking."
            ],
            "when_to_escalate": "If the person cannot make any sound, turns blue/purple, or collapses, call 112 immediately and follow dispatcher CPR guidance."
        },
        "hi": {
            "title": "गले में कुछ अटकना (दम घुटना / चोकिंग) प्राथमिक उपचार",
            "do_this_now": [
                "जांचें कि क्या व्यक्ति खांस या बोल पा रहा है। यदि वह खांस सकता है, तो उसे लगातार खांसने के लिए कहें।",
                "वयस्क या 1 वर्ष से बड़े बच्चे के लिए: पीछे खड़े होकर आगे झुकाएं और दोनों कंधों के बीच हथेली से 5 बार पीठ पर थपकी दें।",
                "यदि वस्तु न निकले: नाभि के ठीक ऊपर मुट्ठी रखकर 5 बार पेट को अंदर और ऊपर की ओर दबाएं (हेमलिच पैंतरा)।",
                "1 वर्ष से छोटे शिशु के लिए: शिशु को हाथ पर पेट के बल लिटाकर पीठ पर 5 हल्की थपकियां दें, फिर पलटकर छाती पर 2 उंगलियों से 5 बार दबाएं। शिशु के पेट पर कभी जोर न लगाएं।"
            ],
            "next_action": [
                "वस्तु निकलने तक 5 पीठ की थपकियां और 5 पेट के दबाव का क्रम दोहराएं।",
                "यदि व्यक्ति बेहोश हो जाए: तुरंत जमीन पर लिटाएं और सीपीआर शुरू करें।"
            ],
            "what_not_to_do": [
                "मुंह में अंधी उंगली डालकर वस्तु निकालने की कोशिश न करें।",
                "1 साल से छोटे बच्चे के पेट पर दबाव न डालें।",
                "पानी या खाना न दें।"
            ],
            "when_to_escalate": "यदि होंठ नीले पड़ने लगें या व्यक्ति बेहोश हो जाए तो तुरंत 112 पर संपर्क करें।"
        },
        "bn": {
            "title": "গলায় কিছু আটকে দমবন্ধ হওয়া (Choking) প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "ব্যক্তি কাশতে বা কথা বলতে পারছে কি না দেখুন। কাশতে পারলে জোরে কাশতে বলুন।",
                "প্রাপ্তবয়স্ক ও ১ বছরের বড় শিশুর ক্ষেত্রে: রোগীকে সামনে ঝুঁকিয়ে পিঠের দুটি কাঁধের মাঝে হাতের তালু দিয়ে ৫ বার চাপড় দিন।",
                "তাতে কাজ না হলে নাভির ওপরে হাত রেখে ভেতরের ও ওপরের দিকে ৫ বার পেটে চাপ দিন।",
                "১ বছরের কম বয়সী শিশুর ক্ষেত্রে: পেটে চাপ দেবেন না। হাতে উপুড় করে পিঠে ৫ বার আলতো চাপড় দিন ও উল্টে বুকে ২ আঙুল দিয়ে চাপ দিন।"
            ],
            "next_action": [
                "বস্তুটি বের না হওয়া পর্যন্ত ৫ বার পিঠে চাপড় ও ৫ বার পেটে চাপ প্রক্রিয়া চালান।",
                "অজ্ঞান হয়ে গেলে মাটিতে শুইয়ে সিপিআর শুরু করুন।"
            ],
            "what_not_to_do": [
                "না দেখে মুখের ভেতর আঙুল ঢুকিয়ে কিছু বের করার চেষ্টা করবেন না।",
                "শিশুর পেটে চাপ দেবেন না।",
                "জল বা খাবার খাওয়াবেন না।"
            ],
            "when_to_escalate": "মুখ বা ঠোঁট নীল হয়ে গেলে সঙ্গে সঙ্গে ১১২-তে জানান।"
        },
        "mr": {
            "title": "घशात काही अडकल्यास (चोकिंग) प्रथमोपचार",
            "do_this_now": [
                "व्यक्ती खोकू शकत असल्यास त्याला जोरात खोकण्यास सांगा.",
                "मोठ्या व्यक्तीसाठी: पुढे वाकवून पाठीवर दोन्ही खांद्यांच्या मध्ये ५ वेळा हाताने थोपटा.",
                "वस्तु न निघाल्यास: नाभीच्या वर हात ठेवून पोटावर ५ वेळा वरच्या दिशेने दाब द्या.",
                "१ वर्षाखालील बाळासाठी: पोटावर दाब देऊ नका; हातावर पालथे ठेवून पाठीवर ५ हळूवार थापा द्या."
            ],
            "next_action": [
                "वस्तु निघेपर्यंत ५ पाठीवर थापा आणि ५ पोटावर दाब ही प्रक्रिया चालू ठेवा.",
                "बेशुद्ध पडल्यास ताबडतोब जमिनीवर झोपवून सीपीआर सुरू करा."
            ],
            "what_not_to_do": [
                "तोंडात न पाहता बोट घालू नका.",
                "लहान बाळाच्या पोटावर दाब देऊ नका.",
                "पाणी पाजू नका."
            ],
            "when_to_escalate": "चेहरा किंवा ओठ निळे पडल्यास त्वरित 112 वर कॉल करा."
        },
        "ta": {
            "title": "தொண்டையில் அடைப்பு ஏற்பட்டால் முதலுதவி",
            "do_this_now": [
                "இருமும்படி பாதிக்கப்பட்டவரைக் கேட்டுக் கொள்ளுங்கள்.",
                "பெரியவர்களுக்கு: நபரை முன்னால் சாய்த்து முதுகில் 5 முறை தட்டவும்.",
                "பொருளை வெளியேற்ற முடியாவிட்டால்: தொப்புளுக்கு மேலே கையை வைத்து வயிற்றை மேல்நோக்கி 5 முறை அழுத்தவும்.",
                "1 வயதுக்குட்பட்ட குழந்தைகளுக்கு: வயிற்றில் அழுத்தக் கூடாது; கையில் குப்புறப் படுக்க வைத்து முதுகில் 5 முறை தட்டவும்."
            ],
            "next_action": [
                "பொருள் வெளிவரும் வரை இந்த முறையைத் தொடரவும்.",
                "மயக்கமடைந்தால் உடனே கீழே படுக்க வைத்து CPR தொடங்கவும்."
            ],
            "what_not_to_do": [
                "வாயினுள் கண்மூடித்தனமாக விரலை நுழைக்க வேண்டாம்.",
                "சிறு குழந்தைகளுக்கு வயிற்றில் அழுத்தக் கூடாது.",
                "தண்ணீர் புகட்ட வேண்டாம்."
            ],
            "when_to_escalate": "உதடுகள் நீல நிறமாக மாறினாலோ அல்லது மயக்கமடைந்தாலோ உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "గొంతులో ఏదైనా అడ్డుపడినప్పుడు ప్రథమ చికిత్స",
            "do_this_now": [
                "బాధితుడు దగ్గగలిగితే గట్టిగా దగ్గమని చెప్పండి.",
                "పెద్దవారికి: ముందుకు వంచి వీపుపై భుజాల మధ్య 5 సార్లు గట్టిగా తట్టండి.",
                "వస్తువు రాకపోతే: బొడ్డు పైభాగంలో పిడికిలి పెట్టి 5 సార్లు లోపలికి, పైకి ఒత్తండి.",
                "1 సంవత్సరం లోపు శిశువులకు: పొట్టపై ఒత్తవద్దు; చేతిపై బోర్లా పడుకోబెట్టి వీపుపై 5 సార్లు సున్నితంగా తట్టండి."
            ],
            "next_action": [
                "వస్తువు బయటకు వచ్చేవరకు ఈ ప్రక్రియను కొనసాగించండి.",
                "స్పృహ కోల్పోతే వెంటనే నేలపై పడుకోబెట్టి CPR ప్రారంభించండి."
            ],
            "what_not_to_do": [
                "నోటిలో వేలు పెట్టి తీయడానికి ప్రయత్నించవద్దు.",
                "చిన్న పిల్లల పొట్టపై ఒత్తవద్దు.",
                "నీరు లేదా ఆహారం ఇవ్వవద్దు."
            ],
            "when_to_escalate": "పెదవులు నీలంగా మారినా, స్పృహ తప్పినా వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "cardiac_emergency": {
        "en": {
            "title": "Suspected Cardiac Emergency / Severe Chest Pain",
            "do_this_now": [
                "Help the person sit down in a comfortable position, ideally supported sitting on the floor with knees bent (W-position).",
                "Loosen tight clothing around the neck, chest, and waist.",
                "Keep the person completely resting and calm. Physical exertion increases strain on the heart.",
                "Stay with the person while the ambulance is approaching and follow emergency dispatcher instructions."
            ],
            "next_action": [
                "Continuously monitor their breathing and consciousness level.",
                "If they have their own prescribed emergency heart medication (like nitroglycerin spray/tablet) and are conscious, assist them in taking it as previously prescribed by their doctor."
            ],
            "what_not_to_do": [
                "DO NOT allow the person to walk, climb stairs, or exert themselves.",
                "DO NOT give aspirin if they are allergic, bleeding, or if dispatcher has not advised it.",
                "DO NOT leave the person alone."
            ],
            "when_to_escalate": "If the person collapses or stops breathing, immediately place them on their back, call 112, and start continuous CPR chest compressions."
        },
        "hi": {
            "title": "सीने में तेज दर्द / दिल का दौरा संदेह प्राथमिक उपचार",
            "do_this_now": [
                "मरीज को तुरंत फर्श या कुर्सी पर पीठ के सहारे घुटने मोड़कर बैठाएं।",
                "गले और कमर के कसे हुए कपड़े ढीले कर दें।",
                "मरीज को पूरी तरह शांत और स्थिर रखें, बिल्कुल चलने न दें।",
                "112 डिस्पैचर के निर्देशों का पालन करें।"
            ],
            "next_action": [
                "सांस और होश की लगातार निगरानी करते रहें।",
                "यदि डॉक्टर द्वारा पहले से लिखी अपनी दवा उनके पास है, तो लेने में मदद करें।"
            ],
            "what_not_to_do": [
                "मरीज को चलने या सीढ़ियां चढ़ने न दें।",
                "बिना सलाह कोई नई दवा न दें।",
                "मरीज को अकेला न छोड़ें।"
            ],
            "when_to_escalate": "यदि मरीज बेहोश हो जाए या सांस बंद हो जाए तो तुरंत 112 पर बताएं और सीपीआर शुरू करें।"
        },
        "bn": {
            "title": "বুকে তীব্র ব্যথা বা হার্টের জরুরি অবস্থা",
            "do_this_now": [
                "রোগীকে আরামদায়ক অবস্থানে বসিয়ে দিন (হাঁটু মুড়ে হেলান দিয়ে বসা সবচেয়ে ভালো)।",
                "গলা এবং কোমরের টাইট পোশাক আলগা করে দিন।",
                "রোগীকে সম্পূর্ণ শান্ত রাখুন, কোনো রকম হাঁটাহাঁটি করতে দেবেন না।",
                "ডিসপ্যাচারের পরামর্শ অনুসরণ করুন।"
            ],
            "next_action": [
                "অনবরত শ্বাসপ্রশ্বাস পর্যবেক্ষণ করুন।",
                "ডাক্তারের আগে থেকে নির্দেশিত নিজস্ব ওষুধ থাকলে নিতে সাহায্য করুন।"
            ],
            "what_not_to_do": [
                "রোগীকে হাঁটতে বা সিঁড়ি উঠতে দেবেন না।",
                "রোগীকে একা রেখে যাবেন না।"
            ],
            "when_to_escalate": "রোগী অজ্ঞান হলে মাটিতে শুইয়ে অবিলম্বে ১১২-তে জানান ও সিপিআর শুরু করুন।"
        },
        "mr": {
            "title": "छातीत तीव्र वेदना / हृदयविकार प्रथमोपचार",
            "do_this_now": [
                "रुग्णाला जमिनीवर किंवा खुर्चीवर आधार देऊन बसवा, गुडघे थोडे मुडपून ठेवा.",
                "गळ्याभोवतीचे आणि कमरेचे घट्ट कपडे सैल करा.",
                "रुग्णाला शांत ठेवा, हालचाल करू देऊ नका.",
                "डिस्पॅचरच्या सूचना पाळा."
            ],
            "next_action": [
                "श्वासावर सतत लक्ष ठेवा.",
                "डॉक्टरांनी आधीच दिलेले स्वतःचे औषध असल्यास ते घेण्यास मदत करा."
            ],
            "what_not_to_do": [
                "रुग्णाला चालण्यास परवानगी देऊ नका.",
                "रुग्णाला एकटे सोडू नका."
            ],
            "when_to_escalate": "रुग्ण बेशुद्ध पडल्यास त्वरित 112 वर कॉल करा आणि सीपीआर सुरू करा."
        },
        "ta": {
            "title": "நெஞ்சு வலி / இதய அவசரநிலை முதலுதவி",
            "do_this_now": [
                "பாதிக்கப்பட்டவரை உடனே தரையில் சாய்வாக உட்கார வைக்கவும்.",
                "இறுக்கமான ஆடைகளைத் தளர்த்தவும்.",
                "அவரை முழு ஓய்வில் வைக்கவும், நடக்க விடாதீர்கள்.",
                "அவசர சேவை வழிகாட்டுதலைப் பின்பற்றவும்."
            ],
            "next_action": [
                "சுவாசத்தைத் தொடர்ந்து கண்காணிக்கவும்.",
                "மருத்துவர் முன்பே பரிந்துரைத்த மருந்து இருந்தால் எடுக்க உதவலாம்."
            ],
            "what_not_to_do": [
                "நடக்கவோ படிகளில் ஏறவோ அனுமதிக்காதீர்கள்.",
                "தனியாக விட்டுச் செல்ல வேண்டாம்."
            ],
            "when_to_escalate": "மயக்கமடைந்தாலோ அல்லது சுவாசம் நின்றாலோ உடனே 112 ஐ அழைத்து CPR தொடங்கவும்."
        },
        "te": {
            "title": "తీవ్ర గుండె నొప్పి ప్రథమ చికిత్స",
            "do_this_now": [
                "బాధితుడిని కింద కూర్చోబెట్టి వీపుకు ఆసరా ఇవ్వండి.",
                "బిగుతుగా ఉన్న దుస్తులను వదులు చేయండి.",
                "బాధితుడిని ప్రశాంతంగా ఉంచండి, నడవనివ్వవద్దు.",
                "డిస్పాచర్ సూచనలను పాటించండి."
            ],
            "next_action": [
                "శ్వాసను ఎప్పటికప్పుడు గమనించండి.",
                "వైద్యులు ముందే సూచించిన అత్యవసర మందులు ఉంటే వేసుకోవడానికి సహాయపడండి."
            ],
            "what_not_to_do": [
                "నడవడం లేదా మెట్లు ఎక్కడం చేయనివ్వవద్దు.",
                "బాధితుడిని ఒంటరిగా వదిలి వెళ్లవద్దు."
            ],
            "when_to_escalate": "స్పృహ తప్పినా లేదా శ్వాస ఆగినా వెంటనే 112 కు కాల్ చేసి CPR ప్రారంభించండి."
        }
    },
    "breathing_difficulty": {
        "en": {
            "title": "Severe Breathing Difficulty First-Aid",
            "do_this_now": [
                "Help the person sit upright and lean slightly forward, resting their elbows on their knees or a table.",
                "Ensure maximum fresh air circulation (open doors and windows; ask bystanders to step back).",
                "Loosen tight clothing around the collar, chest, and waist.",
                "If they have a prescribed asthma rescue inhaler, assist them in taking it calmly."
            ],
            "next_action": [
                "Encourage slow, calm breathing through pursed lips.",
                "Remain with the person continuously until paramedics arrive."
            ],
            "what_not_to_do": [
                "DO NOT force the person to lie flat, as this makes breathing much harder.",
                "DO NOT crowd the person or create panic.",
                "DO NOT give food or drink."
            ],
            "when_to_escalate": "If lips, tongue, or fingertips turn blue/grey, or speech becomes impossible, notify 112 immediately."
        },
        "hi": {
            "title": "सांस लेने में भारी तकलीफ प्राथमिक उपचार",
            "do_this_now": [
                "मरीज को सीधा बैठाएं और हल्का सा आगे की ओर झुकने दें।",
                "खिड़की-दरवाजे खोल दें ताकि ताजी हवा आ सके और भीड़ को दूर रखें।",
                "गले और सीने के कपड़े ढीले करें।",
                "यदि मरीज का अपना इनहेलर (दमे का पंप) है, तो उसे लेने में सहायता करें।"
            ],
            "next_action": [
                "मरीज को होंठ सिकोड़कर धीरे-धीरे सांस लेने को कहें।",
                "एम्बुलेंस आने तक साथ बने रहें।"
            ],
            "what_not_to_do": [
                "मरीज को सीधा पीठ के बल न लिटाएं, इससे सांस लेना और कठिन हो जाता है।",
                "भीड़ न लगाएं।"
            ],
            "when_to_escalate": "यदि होंठ या नाखून नीले पड़ने लगें, तो तुरंत 112 पर सूचना दें।"
        },
        "bn": {
            "title": "শ্বাসকষ্টের জরুরি প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "রোগীকে সোজা করে বসান এবং সামনের দিকে সামান্য ঝুঁকিয়ে রাখুন।",
                "ঘরের জানালা খুলে দিন যাতে পর্যাপ্ত বাতাস আসে। ভিড় সরিয়ে দিন।",
                "গলার পোশাক আলগা করুন।",
                "রোগীর ইনহেলার থাকলে তা ব্যবহারে সাহায্য করুন।"
            ],
            "next_action": ["ধীরে ধীরে শ্বাস নিতে বলুন এবং পাশে থাকুন।"],
            "what_not_to_do": ["রোগীকে চিত করে শুইয়ে দেবেন না।"],
            "when_to_escalate": "নখ বা ঠোঁট নীল হলে অবিলম্বে ১১২-তে জানান।"
        },
        "mr": {
            "title": "तीव्र धाप लागणे / श्वास घेण्यास त्रास प्रथमोपचार",
            "do_this_now": [
                "रुग्णाला सरळ बसवा आणि थोडे पुढे झुकू द्या.",
                "हवेसाठी खिडक्या उघडा आणि गर्दी दूर ठेवा.",
                "कपडे सैल करा.",
                "दम्याचा इनहेलर असल्यास तो घेण्यास मदत करा."
            ],
            "next_action": ["रुग्णाला हळूहळू श्वास घेण्यास सांगा."],
            "what_not_to_do": ["रुग्णाला सपाट झोपवू नका."],
            "when_to_escalate": "ओठ किंवा नखे निळी पडल्यास ताबडतोब 112 ला कळवा."
        },
        "ta": {
            "title": "கடுமையான மூச்சுத்திணறல் முதலுதவி",
            "do_this_now": [
                "பாதிக்கப்பட்டவரை நேராக உட்கார வைத்து லேசாக முன்னோக்கி சாய்க்கவும்.",
                "நல்ல காற்றோட்டத்தை ஏற்படுத்தவும், கூட்டத்தைக் கலைக்கவும்.",
                "ஆடைகளைத் தளர்த்தவும்.",
                "இன்ஹேலர் இருந்தால் பயன்படுத்த உதவவும்."
            ],
            "next_action": ["மெதுவாக மூச்சை உள்ளிழுத்து விடச் சொல்லவும்."],
            "what_not_to_do": ["நேராக மல்லாக்கப் படுக்க வைக்க வேண்டாம்."],
            "when_to_escalate": "உதடுகள் நீலமாக மாறினால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "తీవ్ర శ్వాస సమస్యలకు ప్రథమ చికిత్స",
            "do_this_now": [
                "బాధితుడిని నిటారుగా కూర్చోబెట్టి కొద్దిగా ముందుకు వంచండి.",
                "గాలి బాగా ఆడేలా కిటికీలు తెరవండి, గుంపును తొలగించండి.",
                "దుస్తులను వదులు చేయండి.",
                "ఇన్హేలర్ ఉంటే తీసుకోవడానికి సహాయపడండి."
            ],
            "next_action": ["నెమ్మదిగా శ్వాస తీసుకోమని చెప్పండి."],
            "what_not_to_do": ["వెల్లకిలా పడుకోబెట్టవద్దు."],
            "when_to_escalate": "పెదవులు లేదా గోళ్లు నీలంగా మారితే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "road_accident": {
        "en": {
            "title": "Road Traffic Accident First-Aid",
            "do_this_now": [
                "Ensure SCENE SAFETY first: Make sure oncoming traffic is warned and you are not in danger of being struck.",
                "Keep the injured person completely STILL. DO NOT move them unless there is immediate danger (e.g. fire, sinking water).",
                "Support the head and neck in the exact position found to protect the spinal cord.",
                "Control any severe spurting bleeding with firm direct pressure using a clean cloth."
            ],
            "next_action": [
                "Cover the person to prevent shock and hypothermia.",
                "Speak gently to the person to keep them calm and conscious.",
                "Provide exact location markers or landmarks to incoming ambulance crews."
            ],
            "what_not_to_do": [
                "DO NOT move the person's neck or back.",
                "DO NOT remove a motorcycle helmet unless the airway is blocked and they cannot breathe.",
                "DO NOT give liquids or food."
            ],
            "when_to_escalate": "If the person becomes unresponsive or stops breathing, immediately inform the 112 dispatcher."
        },
        "hi": {
            "title": "सड़क दुर्घटना प्राथमिक उपचार",
            "do_this_now": [
                "सबसे पहले अपनी और घटनास्थल की सुरक्षा सुनिश्चित करें।",
                "घायल व्यक्ति को बिल्कुल न हिलाएं, जब तक कि आग या डूबने जैसा गंभीर खतरा न हो।",
                "गर्दन और सिर को उसी स्थिति में स्थिर रखें ताकि रीढ़ की हड्डी को नुकसान न पहुंचे।",
                "यदि तेज खून बह रहा हो, तो साफ कपड़े से दबाएं।"
            ],
            "next_action": [
                "घायल को चादर से ढककर गर्म रखें।",
                "घायल से बात करते रहें ताकि उसका होश बना रहे।"
            ],
            "what_not_to_do": [
                "घायल को जबरन उठाने या हिलाने की कोशिश न करें।",
                "हेलमेट न उतारें जब तक कि सांस रुक न रही हो।",
                "पानी या दवा न दें।"
            ],
            "when_to_escalate": "यदि सांस रुक जाए या गंभीर बेहोशी हो तो तुरंत 112 को सूचित करें।"
        },
        "bn": {
            "title": "সড়ক দুর্ঘটনা প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "প্রথমে নিজের ও চারপাশের নিরাপত্তা নিশ্চিত করুন।",
                "গুরুতর বিপদ (যেমন আগুন) না থাকলে আহত ব্যক্তিকে নড়াচড়া করাবেন না।",
                "মাথা ও ঘাড় স্থির রাখুন যাতে মেরুদণ্ডে আঘাত না লাগে।",
                "রক্তক্ষরণ হলে পরিষ্কার কাপড় দিয়ে শক্ত করে চেপে ধরুন।"
            ],
            "next_action": ["রোগীকে আশ্বস্ত করুন এবং ঢেকে রাখুন।"],
            "what_not_to_do": [
                "রোগীকে জোর করে তোলার চেষ্টা করবেন না।",
                "হেলমেট খোলার চেষ্টা করবেন না।"
            ],
            "when_to_escalate": "শ্বাস বন্ধ হলে সাথে সাথে ১১২-তে জানান।"
        },
        "mr": {
            "title": "रस्ता अपघात प्रथमोपचार",
            "do_this_now": [
                "प्रथम स्वतःची आणि जागेची सुरक्षितता तपासा.",
                "रुग्णाला आग किंवा इतर धोका नसल्यास अजिबात हलवू नका.",
                "मान आणि डोके जागेवर स्थिर ठेवा.",
                "रक्तस्त्राव होत असल्यास दाबा."
            ],
            "next_action": ["रुग्णाला उबदार ठेवा आणि बोलत राहा."],
            "what_not_to_do": ["मानेला किंवा पाठीला झटका देऊ नका.", "हेल्मेट काढू नका."],
            "when_to_escalate": "श्वास थांबल्यास त्वरित 112 वर संपर्क साधा."
        },
        "ta": {
            "title": "சாலை விபத்து முதலுதவி",
            "do_this_now": [
                "முதலில் உங்களைச் சுற்றியுள்ள பாதுகாப்பை உறுதிப்படுத்தவும்.",
                "தீ போன்ற அவசர ஆபத்து இல்லாவிட்டால் காயமடைந்தவரை அசைக்காதீர்கள்.",
                "கழுத்து மற்றும் தலையை அசைக்காமல் நேராகப் பிடிக்கவும்.",
                "இரத்தப்போக்கு இருந்தால் அழுத்திப் பிடிக்கவும்."
            ],
            "next_action": ["பாதிக்கப்பட்டவரைப் போர்வையால் போர்த்தவும்."],
            "what_not_to_do": ["கழுத்தைத் திருப்பவோ அசைக்கவோ கூடாது.", "ஹெல்மெட்டை அகற்ற வேண்டாம்."],
            "when_to_escalate": "சுவாசம் நின்றால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "రోడ్డు ప్రమాదంలో ప్రథమ చికిత్స",
            "do_this_now": [
                "ముందుగా మీ భద్రతను నిర్ధారించుకోండి.",
                "తీవ్రమైన ప్రమాదం (మంటలు వంటివి) లేకపోతే క్షతగాత్రుడిని కదపవద్దు.",
                "మెడ మరియు తలను స్థిరంగా ఉంచండి.",
                "రక్తం కారుతుంటే గుడ్డతో గట్టిగా ఒత్తండి."
            ],
            "next_action": ["బాధితుడిని దుప్పటితో కప్పి మాట్లాడిస్తూ ఉండండి."],
            "what_not_to_do": ["మెడను కదపవద్దు.", "హెల్మెట్‌ను బలవంతంగా తీయవద్దు."],
            "when_to_escalate": "శ్వాస ఆగిపోతే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "burns": {
        "en": {
            "title": "Burns First-Aid",
            "do_this_now": [
                "Cool the burn IMMEDIATELY under cool or lukewarm running tap water for 10 to 20 minutes.",
                "Gently remove jewelry, rings, watches, or loose clothing near the burn BEFORE swelling begins.",
                "Cover the burn loosely with clean plastic cling wrap or a sterile non-stick cloth.",
                "Keep the person warm with a blanket outside the burned area."
            ],
            "next_action": [
                "For chemical burns: Flush continuously with copious clean water for at least 20 minutes.",
                "For electrical burns: Ensure the power source is disconnected before touching the person."
            ],
            "what_not_to_do": [
                "DO NOT apply ice, ice water, butter, oils, toothpaste, or home remedies.",
                "DO NOT burst any blisters.",
                "DO NOT remove clothing that is stuck to the burn."
            ],
            "when_to_escalate": "If the burn covers a large area, involves face/hands/genitals, or was caused by chemicals/electricity, inform 112 immediately."
        },
        "hi": {
            "title": "जलने (बर्न) पर प्राथमिक उपचार",
            "do_this_now": [
                "जले हुए हिस्से पर तुरंत 10 से 20 मिनट तक बहता हुआ ठंडा या सामान्य नल का पानी डालें।",
                "सूजन आने से पहले जले अंग के पास की अंगूठी, घड़ी या ढीले कपड़े आराम से हटा दें।",
                "जले हुए स्थान पर साफ प्लास्टिक रैप या साफ कपड़ा ढीला बांधें।",
                "मरीज को गर्म रखें।"
            ],
            "next_action": ["केमिकल से जलने पर कम से कम 20 मिनट तक लगातार पानी से धोते रहें।"],
            "what_not_to_do": [
                "बर्फ, तेल, मक्खन, टूथपेस्ट या हल्दी बिल्कुल न लगाएं।",
                "फफोलों को न फोड़ें।",
                "चिपके हुए कपड़े को न खींचे।"
            ],
            "when_to_escalate": "यदि बहुत ज्यादा हिस्सा जला हो या चेहरा/हाथ झुलसे हों तो तुरंत 112 को बताएं।"
        },
        "bn": {
            "title": "আগুনে পোড়ার প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "পোড়া জায়গায় অন্তত ১০-২০ মিনিট ধরে সাধারণ ঠান্ডা জলের ধারা দিন।",
                "আংটি, ঘড়ি বা ঢিলে পোশাক দ্রুত খুলে ফেলুন।",
                "পরিষ্কার পাতলা প্লাস্টিক বা গজ দিয়ে আলতো করে ঢেকে রাখুন।"
            ],
            "next_action": ["রাসায়নিক দিয়ে পুড়লে একটানা ২০ মিনিট জল ঢালুন।"],
            "what_not_to_do": [
                "বরফ, তেল, টুথপেস্ট বা মাখন লাগাবেন না।",
                "ফোস্কা ফাটাবেন না।",
                "ত্বকের সাথে আটকে থাকা কাপড় টানবেন না।"
            ],
            "when_to_escalate": "মুখ বা বড় অংশ পুড়ে গেলে অবিলম্বে ১১২-তে জানান।"
        },
        "mr": {
            "title": "भाजल्यास प्रथमोपचार",
            "do_this_now": [
                "भाजलेल्या भागावर त्वरित १० ते २० मिनिटे नळाचे थंड पाणी टाका.",
                "अंगठी, घड्याळ इत्यादी वस्तू सूज येण्यापूर्वी काढून टाका.",
                "स्वच्छ कापडाने सैल झाकून ठेवा."
            ],
            "next_action": ["रासायनिक भाजणे असल्यास २० मिनिटे सतत पाणी टाका."],
            "what_not_to_do": [
                "बर्फ, तेल, टूथपेस्ट लावू नका.",
                "फोड फोडू नका.",
                "त्वचेला चिकटलेले कपडे खेचू नका."
            ],
            "when_to_escalate": "मोठा भाग भाजला असल्यास त्वरित 112 वर कॉल करा."
        },
        "ta": {
            "title": "தீக்காயம் முதலுதவி",
            "do_this_now": [
                "எரிந்த இடத்தில் உடனடியாக 10-20 நிமிடங்கள் குளிர்ந்த நீர் ஊற்றவும்.",
                "மோதிரம், கடிகாரம் போன்றவற்றை உடனே கழற்றவும்.",
                "சுத்தமான துணியால் தளர்வாக மூடவும்."
            ],
            "next_action": ["ரசாயனக் காயம் என்றால் தொடர்ந்து 20 நிமிடங்கள் கழுவவும்."],
            "what_not_to_do": [
                "பனிக்கட்டி, எண்ணெய், டூத்பேஸ்ட் தடவக் கூடாது.",
                "கொப்புளங்களை உடைக்கக் கூடாது.",
                "ஒட்டியிருக்கும் துணியை இழுக்கக் கூடாது."
            ],
            "when_to_escalate": "தீக்காயம் அதிகமாக இருந்தால் உடனடியாக 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "కాలిన గాయాలకు ప్రథమ చికిత్స",
            "do_this_now": [
                "కాలిన భాగంపై వెంటనే 10-20 నిమిషాల పాటు చల్లని నీరు పోయండి.",
                "వాపు రాకముందే ఉంగరాలు, గడియారాలు తొలగించండి.",
                "శుభ్రమైన గుడ్డతో వదులుగా కప్పండి."
            ],
            "next_action": ["రసాయనాలు పడితే కనీసం 20 నిమిషాలు నీటితో కడగండి."],
            "what_not_to_do": [
                "మంచు, నూనె, పేస్ట్ రాయవద్దు.",
                "బొబ్బలను పగలగొట్టవద్దు.",
                "అతుక్కున్న బట్టలను లాగవద్దు."
            ],
            "when_to_escalate": "గాయం పెద్దదైతే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "pregnancy_emergency": {
        "en": {
            "title": "Pregnancy Emergency First-Aid",
            "do_this_now": [
                "Help the pregnant woman lie down comfortably on her LEFT SIDE. (This relieves pressure on major blood vessels and maximizes blood flow to the baby).",
                "Keep her warm, calm, and resting quietly.",
                "If there is bleeding, place a clean sanitary pad or clean towel underneath (do NOT insert anything into the vagina).",
                "Follow any emergency dispatcher guidance while the ambulance is en route."
            ],
            "next_action": [
                "Monitor consciousness, breathing, and pain frequency.",
                "Note down if water has broken or if there is severe headache / visual disturbance."
            ],
            "what_not_to_do": [
                "DO NOT have her lie flat on her back.",
                "DO NOT insert any tampons or cloths inside the birth canal.",
                "DO NOT give any unprescribed medications or pain killers."
            ],
            "when_to_escalate": "If there is heavy bleeding, severe sudden pain, seizures, or loss of consciousness, alert 112 immediately."
        },
        "hi": {
            "title": "गर्भावस्था आपातकालीन प्राथमिक उपचार",
            "do_this_now": [
                "गर्भवती महिला को तुरंत बाईं करवट (LEFT SIDE) लिटाएं। इससे बच्चे तक रक्त संचार बेहतर रहता है।",
                "महिला को शांत और सहज रखें।",
                "यदि रक्तस्राव हो रहा हो, तो साफ कपड़ा या पैड बाहर रखें (अंदर कुछ न डालें)।",
                "112 डिस्पैचर के निर्देशों का पालन करें।"
            ],
            "next_action": ["महिला के दर्द और सांस पर नजर रखें।"],
            "what_not_to_do": [
                "महिला को सीधा पीठ के बल न लिटाएं।",
                "कोई भी दर्द निवारक या अन्य दवा न दें।"
            ],
            "when_to_escalate": "यदि तेज रक्तस्राव, असहनीय दर्द या बेहोशी हो तो तुरंत 112 को बताएं।"
        },
        "bn": {
            "title": "গর্ভাবস্থার জরুরি প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "গর্ভবতী নারীকে অবিলম্বে বাঁ-পাশে কাত করে শুইয়ে দিন। এতে রক্ত চলাচল স্বাভাবিক থাকে।",
                "তাঁকে শান্ত ও আশ্বস্ত রাখুন।",
                "রক্তপাত হলে পরিষ্কার প্যাড বা কাপড় বাইরে ব্যবহার করুন।"
            ],
            "next_action": ["শ্বাসপ্রশ্বাস ও ব্যথার গতি লক্ষ্য করুন।"],
            "what_not_to_do": ["চিত করে পিঠের ওপর শোয়াবেন না।", "কোনো ওষুধ খাওয়াবেন না।"],
            "when_to_escalate": "প্রচুর রক্তপাত বা খিঁচুনি হলে ১১২-তে দ্রুত জানান।"
        },
        "mr": {
            "title": "गरोदरपणातील आपत्कालीन प्रथमोपचार",
            "do_this_now": [
                "गरोदर महिलेला लगेच डाव्या कुशीवर झोपवा.",
                "शांत व उबदार ठेवा.",
                "रक्तस्त्राव होत असल्यास स्वच्छ पॅड वापरा."
            ],
            "next_action": ["श्वास आणि वेदनेवर लक्ष ठेवा."],
            "what_not_to_do": ["पाठीवर उताणे झोपवू नका.", "कोणतेही औषध देऊ नका."],
            "when_to_escalate": "तीव्र रक्तस्त्राव किंवा बेशुद्धी असल्यास त्वरित 112 ला सांगा."
        },
        "ta": {
            "title": "கர்ப்பகால அவசரநிலை முதலுதவி",
            "do_this_now": [
                "கர்ப்பிணிப் பெண்ணை உடனடியாக இடது பக்கமாக ஒருக்களித்துப் படுக்க வைக்கவும்.",
                "அமைதியாக ஓய்வெடுக்கச் செய்யவும்.",
                "இரத்தப்போக்கு இருந்தால் சுத்தமான துணியை வைக்கவும்."
            ],
            "next_action": ["சுவாசத்தையும் வலியையும் கவனிக்கவும்."],
            "what_not_to_do": ["மல்லாக்கப் படுக்க வைக்க வேண்டாம்.", "எந்த மருந்தும் கொடுக்க வேண்டாம்."],
            "when_to_escalate": "கடுமையான இரத்தப்போக்கு அல்லது வலி இருந்தால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "గర్భధారణ అత్యవసర ప్రథమ చికిత్స",
            "do_this_now": [
                "గర్భిణీని వెంటనే ఎడమ వైపుకు తిరిగి పడుకోబెట్టండి.",
                "ప్రశాంతంగా ఉంచండి.",
                "రక్తస్రావం ఉంటే శుభ్రమైన ప్యాడ్ లేదా గుడ్డ ఉంచండి."
            ],
            "next_action": ["నొప్పులను మరియు శ్వాసను గమనిస్తూ ఉండండి."],
            "what_not_to_do": ["వెల్లకిలా పడుకోబెట్టవద్దు.", "ఎలాంటి మందులు ఇవ్వవద్దు."],
            "when_to_escalate": "తీవ్ర రక్తస్రావం లేదా స్పృహ తప్పితే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "stroke_symptoms": {
        "en": {
            "title": "Suspected Stroke (F.A.S.T. Assessment)",
            "do_this_now": [
                "Recognize F.A.S.T. signs: Face drooping (ask to smile), Arm weakness (ask to raise both arms), Speech difficulty (ask to repeat a simple phrase), Time is critical.",
                "Keep the person resting comfortably with their head slightly elevated.",
                "If they are drowsy or vomiting, turn them gently onto their side to keep the airway clear.",
                "Note the EXACT TIME when symptoms first started to tell the ambulance crew."
            ],
            "next_action": ["Keep them calm and quiet. Reassure them that help is arriving."],
            "what_not_to_do": [
                "DO NOT give aspirin, food, water, or any medication.",
                "DO NOT let them sleep or exert themselves."
            ],
            "when_to_escalate": "If breathing changes or responsiveness drops, alert 112 immediately."
        },
        "hi": {
            "title": "लकवा / स्ट्रोक का संदेह प्राथमिक उपचार (F.A.S.T)",
            "do_this_now": [
                "लक्षण पहचानें: चेहरा एक तरफ लटकना, हाथ में कमजोरी, बोलने में लड़खड़ाहट।",
                "मरीज को सिर थोड़ा ऊंचा रखकर आराम से लिटाएं।",
                "लक्षण किस समय शुरू हुए, वह सही समय याद रखें।"
            ],
            "next_action": ["मरीज को शांत रखें और ढांढस बंधाएं।"],
            "what_not_to_do": ["पानी, एस्पिरिन या कोई भी दवा न दें।", "मरीज को सोने न दें।"],
            "when_to_escalate": "होश खोने पर तुरंत 112 पर सूचना दें।"
        },
        "bn": {
            "title": "স্ট্রোকের লক্ষণ প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "লক্ষণ দেখুন: মুখের একপাশ বেঁকে যাওয়া, হাত তুলতে না পারা, অস্পষ্ট কথা।",
                "রোগীর মাথা সামান্য উঁচু করে শুইয়ে দিন।",
                "কখন লক্ষণ শুরু হয়েছে সময়টি লিখে রাখুন।"
            ],
            "next_action": ["রোগীকে শান্ত রাখুন।"],
            "what_not_to_do": ["জল বা কোনো ওষুধ দেবেন না।"],
            "when_to_escalate": "অজ্ঞান হলে ১১২-তে জানান।"
        },
        "mr": {
            "title": "पक्षाघात / स्ट्रोक संशय प्रथमोपचार",
            "do_this_now": [
                "लक्षणे तपासा: तोंड वाकडे होणे, हात वर न होणे, बोलताना अडखळणे.",
                "डोके थोडे वर ठेवून शांत झोपवा.",
                "लक्षणे कधी सुरू झाली तो वेळ नोंदवा."
            ],
            "next_action": ["रुग्णाला धीर द्या."],
            "what_not_to_do": ["पाणी किंवा औषध देऊ नका."],
            "when_to_escalate": "बेशुद्ध झाल्यास 112 वर संपर्क साधा."
        },
        "ta": {
            "title": "பக்கவாதம் (Stroke) முதலுதவி",
            "do_this_now": [
                "அறிகுறிகளைச் சரிபார்க்கவும்: முகம் ஒருபக்கம் கோணுதல், கையைத் தூக்க முடியாமை, பேச்சுக் குளறுதல்.",
                "தலையை லேசாக உயர்த்திப் படுக்க வைக்கவும்.",
                "அறிகுறி தொடங்கிய நேரத்தைக் குறித்துக்கொள்ளவும்."
            ],
            "next_action": ["அமைதியாக இருக்க உதவவும்."],
            "what_not_to_do": ["தண்ணீர் அல்லது மாத்திரை கொடுக்கக் கூடாது."],
            "when_to_escalate": "மயக்கமடைந்தால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "పక్షవాతం (Stroke) ప్రథమ చికిత్స",
            "do_this_now": [
                "లక్షణాలను గుర్తించండి: ముఖం వంకరపోవడం, చేయి ఎత్తలేకపోవడం, మాట తడబడటం.",
                "తల కొద్దిగా పైకి పెట్టి పడుకోబెట్టండి.",
                "సమస్య ఎప్పుడు మొదలైందో సమయాన్ని గుర్తించండి."
            ],
            "next_action": ["ప్రశాంతంగా ఉంచండి."],
            "what_not_to_do": ["నీరు లేదా ఎలాంటి మందులు ఇవ్వవద్దు."],
            "when_to_escalate": "స్పృహ కోల్పోతే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "seizure": {
        "en": {
            "title": "Seizure / Convulsion First-Aid",
            "do_this_now": [
                "Clear the surrounding area of sharp, hard, or dangerous objects to protect from injury.",
                "Place something soft (like a folded jacket or towel) under their head.",
                "Time the seizure from when it starts.",
                "Once the jerking stops, gently roll them onto their side into the recovery position to keep the airway open."
            ],
            "next_action": ["Stay by their side calmly until they fully regain consciousness."],
            "what_not_to_do": [
                "DO NOT restrain the person or hold them down.",
                "DO NOT put anything in their mouth (no spoons, cloth, or fingers).",
                "DO NOT offer water or food until they are completely awake and alert."
            ],
            "when_to_escalate": "If the seizure lasts more than 5 minutes, repeats without regaining consciousness, or occurs in water or pregnancy, alert 112 immediately."
        },
        "hi": {
            "title": "दौरा पड़ना (मिर्गी / सीज़र) प्राथमिक उपचार",
            "do_this_now": [
                "आसपास की नुकीली या कठोर चीजें तुरंत हटा दें।",
                "सिर के नीचे कोई मुलायम कपड़ा या मुड़ा हुआ तौलिया रखें।",
                "दौरा कितने समय तक चला, समय पर नजर रखें।",
                "दौरा रुकने पर मरीज को करवट दिलाकर लिटाएं।"
            ],
            "next_action": ["मरीज के पूरी तरह होश में आने तक पास रहें।"],
            "what_not_to_do": [
                "मरीज को जबरन पकड़ने या रोकने की कोशिश न करें।",
                "मुंह में चम्मच, कपड़ा या उंगली बिल्कुल न डालें।",
                "जूता या प्याज न सुंघाएं।"
            ],
            "when_to_escalate": "यदि दौरा 5 मिनट से अधिक चले तो तुरंत 112 को बताएं।"
        },
        "bn": {
            "title": "খিঁচুনি / মৃগীরোগের প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "কাছের ধারালো জিনিসপত্র সরিয়ে দিন।",
                "মাথার নিচে নরম কাপড় রাখুন।",
                "খিঁচুনি থামলে রোগীকে একপাশে কাত করে শুইয়ে দিন।"
            ],
            "next_action": ["জ্ঞান না ফেরা পর্যন্ত পাশে থাকুন।"],
            "what_not_to_do": ["রোগীকে চেপে ধরবেন না।", "মুখে চামচ বা কোনো কিছু দেবেন না।"],
            "when_to_escalate": "খিঁচুনি ৫ মিনিটের বেশি স্থায়ী হলে ১১২-তে জানান।"
        },
        "mr": {
            "title": "फिट येणे / फेफरे प्रथमोपचार",
            "do_this_now": [
                "आसपासच्या धारदार वस्तू बाजूला करा.",
                "डोक्याखाली मऊ कापड ठेवा.",
                "झटके थांबल्यावर एका कुशीवर वळवून झोपवा."
            ],
            "next_action": ["शुद्धीवर येईपर्यंत जवळ राहा."],
            "what_not_to_do": ["रुग्णाला दाबून धरू नका.", "तोंडात चमचा किंवा बोट घालू नका."],
            "when_to_escalate": "दौरा ५ मिनिटांपेक्षा जास्त चालल्यास 112 वर संपर्क साधा."
        },
        "ta": {
            "title": "வலிப்பு (Seizure) முதலுதவி",
            "do_this_now": [
                "அருகிலுள்ள ஆபத்தான பொருட்களை அகற்றவும்.",
                "தலைக்கு அடியில் மென்மையான துணியை வைக்கவும்.",
                "வலிப்பு நின்றதும் ஒருக்களித்துப் படுக்க வைக்கவும்."
            ],
            "next_action": ["நினைவு திரும்பும் வரை அருகிலேயே இருக்கவும்."],
            "what_not_to_do": ["நபரை அமுக்கிப் பிடிக்கக் கூடாது.", "வாயில் இரும்பு, சாவி அல்லது விரலை வைக்கக் கூடாது."],
            "when_to_escalate": "வலிப்பு 5 நிமிடங்களுக்கு மேல் நீடித்தால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "ఫిట్స్ (మూర్ఛ) ప్రథమ చికిత్స",
            "do_this_now": [
                "చుట్టూ ఉన్న పదునైన వస్తువులను తొలగించండి.",
                "తల కింద మెత్తటి గుడ్డ ఉంచండి.",
                "ఫిట్స్ ఆగిన తర్వాత ఒక పక్కకు తిప్పి పడుకోబెట్టండి."
            ],
            "next_action": ["స్పృహ వచ్చేవరకు దగ్గరే ఉండండి."],
            "what_not_to_do": ["బాధితుడిని అదిమి పట్టుకోవద్దు.", "నోటిలో తాళంచెవులు లేదా వస్తువులు పెట్టవద్దు."],
            "when_to_escalate": "ఫిట్స్ 5 నిమిషాల కంటే ఎక్కువ ఉంటే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "fracture_injury": {
        "en": {
            "title": "Suspected Fracture / Bone Injury First-Aid",
            "do_this_now": [
                "Keep the injured limb completely still in the position found.",
                "Support the injured area above and below the suspected break with soft padding or rolled towels.",
                "Apply an ice pack wrapped in a cloth (never ice directly on skin) to reduce swelling.",
                "If bone is piercing the skin (open fracture), cover gently with a clean cloth without pushing it back."
            ],
            "next_action": ["Reassure the person and keep them warm."],
            "what_not_to_do": [
                "DO NOT attempt to straighten, realign, or push broken bones back.",
                "DO NOT let the person bear weight or walk on an injured leg."
            ],
            "when_to_escalate": "If the limb is cold, pale, numb, or turning blue, notify 112 immediately."
        },
        "hi": {
            "title": "हड्डी टूटना (फ्रैक्चर) प्राथमिक उपचार",
            "do_this_now": [
                "चोट लगे अंग को बिल्कुल स्थिर रखें।",
                "अंग के नीचे मुलायम कपड़ा रखकर सहारा दें।",
                "सूजन कम करने के लिए कपड़े में लपेटकर बर्फ से सिकाई करें।",
                "यदि हड्डी बाहर निकली हो, तो साफ कपड़े से ढकें, अंदर धकेलने की कोशिश न करें।"
            ],
            "next_action": ["मरीज को शांत रखें।"],
            "what_not_to_do": ["हड्डी को सीधा करने या बिठाने की कोशिश न करें।", "चोट लगे पैर पर चलने न दें।"],
            "when_to_escalate": "यदि अंग ठंडा या सुन्न पड़ जाए तो 112 को तुरंत बताएं।"
        },
        "bn": {
            "title": "হাড় ভাঙা (ফ্র্যাকচার) প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "আহত অঙ্গ নড়াচড়া করাবেন না।",
                "কাপড় বা তোয়ালের সাপোর্ট দিন।",
                "হাড় বেরিয়ে থাকলে পরিষ্কার কাপড় দিয়ে আলতো করে ঢেকে রাখুন।"
            ],
            "next_action": ["রোগীকে আশ্বস্ত করুন।"],
            "what_not_to_do": ["ভাঙা হাড় সোজা করার চেষ্টা করবেন না।"],
            "when_to_escalate": "অঙ্গ নীল বা অসাড় হলে ১১২-তে জানান।"
        },
        "mr": {
            "title": "हाड मोडणे (फ्रॅक्चर) प्रथमोपचार",
            "do_this_now": [
                "जखमी भाग स्थिर ठेवा.",
                "कापडाचा आधार द्या.",
                "हाड बाहेर आले असल्यास स्वच्छ कपड्याने झाका."
            ],
            "next_action": ["रुग्णाला आधार द्या."],
            "what_not_to_do": ["हाड सरळ करण्याचा प्रयत्न करू नका."],
            "when_to_escalate": "अवयव थंड किंवा सुन्न पडल्यास 112 ला सांगा."
        },
        "ta": {
            "title": "எலும்பு முறிவு முதலுதவி",
            "do_this_now": [
                "காயம்பட்ட பகுதியை அசைக்காமல் வைக்கவும்.",
                "மென்மையான துணியால் தாங்கிப் பிடிக்கவும்.",
                "எலும்பு வெளியே தெரிந்தால் சுத்தமான துணியால் மூடவும்."
            ],
            "next_action": ["அமைதியாக இருக்க உதவவும்."],
            "what_not_to_do": ["எலும்பை நேராக்க முயற்சிக்க வேண்டாம்."],
            "when_to_escalate": "உறுப்பு குளிர்ந்து போனால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "ఎముక విరిగినప్పుడు ప్రథమ చికిత్స",
            "do_this_now": [
                "గాయపడిన భాగాన్ని కదల్చకుండా స్థిరంగా ఉంచండి.",
                "గుడ్డ లేదా మెత్తటి వస్తువుతో ఆసరా ఇవ్వండి.",
                "ఎముక బయటకు వస్తే శుభ్రమైన గుడ్డతో కప్పండి."
            ],
            "next_action": ["ధైర్యం చెప్పండి."],
            "what_not_to_do": ["ఎముకను సరిచేయడానికి ప్రయత్నించవద్దు."],
            "when_to_escalate": "భాగం చల్లబడితే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "child_emergency": {
        "en": {
            "title": "Child Emergency First-Aid",
            "do_this_now": [
                "Determine the child's approximate age (infant < 1 yr vs child 1-8 yrs vs older).",
                "Keep the child calm, close to a parent or caregiver, and warmly wrapped.",
                "Check responsiveness and normal breathing.",
                "For high fever with lethargy: Sponge with lukewarm water (never cold or ice water) and keep lightly dressed."
            ],
            "next_action": ["Follow the emergency dispatcher's pediatric guidance carefully."],
            "what_not_to_do": [
                "DO NOT give adult medications or aspirin to children.",
                "DO NOT use cold water or alcohol rubs for fever.",
                "DO NOT shake a baby or child."
            ],
            "when_to_escalate": "If the child is limp, unresponsive, having difficulty breathing, or turning blue, contact 112 immediately."
        },
        "hi": {
            "title": "बच्चों की आपातकालीन स्थिति में प्राथमिक उपचार",
            "do_this_now": [
                "बच्चे की आयु समझें (1 साल से छोटा शिशु या बड़ा बच्चा)।",
                "बच्चे को माता-पिता के पास रखकर शांत और गर्म रखें।",
                "सांस और होश की जांच करें।",
                "तेज बुखार में गुनगुने पानी की पट्टी करें (बर्फ या बहुत ठंडा पानी न लगाएं)।"
            ],
            "next_action": ["112 डिस्पैचर के निर्देशों का पालन करें।"],
            "what_not_to_do": [
                "बच्चों को बड़ों की दवाइयां न दें।",
                "बच्चे को कभी भी जोर से न हिलाएं।"
            ],
            "when_to_escalate": "यदि बच्चा सुस्त हो, सांस न ले पाए या नीला पड़े तो तुरंत 112 को बताएं।"
        },
        "bn": {
            "title": "শিশুদের জরুরি প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "শিশুকে কাছে রেখে শান্ত রাখুন।",
                "শ্বাসপ্রশ্বাস ও সচেতনতা পরীক্ষা করুন।",
                "তীব্র জ্বরে কুসুম গরম জলের পট্টি দিন।"
            ],
            "next_action": ["ডিসপ্যাচারের পরামর্শ নিন।"],
            "what_not_to_do": ["শিশুকে বড়দের ওষুধ দেবেন না।", "শিশুকে জোরে ঝাঁকাবেন না।"],
            "when_to_escalate": "শিশু নিস্তেজ হলে অবিলম্বে ১১২-তে জানান।"
        },
        "mr": {
            "title": "लहान मुलांसाठी आपत्कालीन प्रथमोपचार",
            "do_this_now": [
                "मुलाला जवळ घेऊन शांत ठेवा.",
                "श्वास आणि हालचाली तपासा.",
                "जास्त ताप असल्यास कोमट पाण्याच्या पट्ट्या ठेवा."
            ],
            "next_action": ["112 डिस्पॅचरच्या सूचना पाळा."],
            "what_not_to_do": ["मोठ्यांची औषधे देऊ नका.", "बाळाला हलवू नका."],
            "when_to_escalate": "मूल प्रतिसाद देत नसल्यास लगेच 112 ला सांगा."
        },
        "ta": {
            "title": "குழந்தைகளுக்கான அவசர முதலுதவி",
            "do_this_now": [
                "குழந்தையை அமைதிப்படுத்தி கதகதப்பாக வைக்கவும்.",
                "சுவாசம் மற்றும் விழிப்புணர்வைச் சரிபார்க்கவும்.",
                "காய்ச்சல் அதிகமாக இருந்தால் வெதுவெதுப்பான நீரில் நனைத்த துணியால் துடைக்கவும்."
            ],
            "next_action": ["அவசர சேவை வழிகாட்டுதலைப் பின்பற்றவும்."],
            "what_not_to_do": ["பெரியவர்களின் மருந்துகளைக் கொடுக்கக் கூடாது.", "குழந்தையை உலுக்கக் கூடாது."],
            "when_to_escalate": "குழந்தை அசைவற்றுப் போனால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "పిల్లల అత్యవసర ప్రథమ చికిత్స",
            "do_this_now": [
                "పిల్లలను దగ్గరకు తీసుకుని ప్రశాంతంగా ఉంచండి.",
                "శ్వాసను గమనించండి.",
                "తీవ్ర జ్వరం ఉంటే గోరువెచ్చని నీటితో శరీరాన్ని తుడవండి."
            ],
            "next_action": ["డిస్పాచర్ సూచనలను పాటించండి."],
            "what_not_to_do": ["పెద్దల మందులు ఇవ్వవద్దు.", "పిల్లలను ఊపవద్దు."],
            "when_to_escalate": "పిల్లలు ప్రతిస్పందించకపోతే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "allergic_reaction": {
        "en": {
            "title": "Severe Allergic Reaction (Anaphylaxis) First-Aid",
            "do_this_now": [
                "If the person has an adrenaline auto-injector (EpiPen) for severe allergies, help them use it into their outer mid-thigh immediately.",
                "Help the person lie flat with their legs elevated. If breathing is difficult, allow them to sit up slightly.",
                "Loosen tight clothing and keep them warm.",
                "Follow emergency dispatcher instructions while the ambulance responds."
            ],
            "next_action": ["Monitor breathing and alertness continuously."],
            "what_not_to_do": [
                "DO NOT have them stand or walk.",
                "DO NOT give oral liquids or pills if breathing is compromised."
            ],
            "when_to_escalate": "If swelling spreads to the throat or breathing becomes noisy or impossible, notify 112 immediately."
        },
        "hi": {
            "title": "गंभीर एलर्जी (एनाफिलेक्सिस) प्राथमिक उपचार",
            "do_this_now": [
                "यदि मरीज के पास अपनी एलर्जी का इंजेक्शन (EpiPen) है, तो जांघ के बाहरी हिस्से में लगाने में मदद करें।",
                "मरीज को लिटाएं और पैर थोड़े ऊंचे रखें। यदि सांस में दिक्कत हो, तो थोड़ा बैठने दें।",
                "कपड़े ढीले करें।"
            ],
            "next_action": ["सांस की लगातार निगरानी करें।"],
            "what_not_to_do": ["मरीज को खड़ा न करें या चलने न दें।"],
            "when_to_escalate": "गले में सूजन या सांस रुकने पर तुरंत 112 को बताएं।"
        },
        "bn": {
            "title": "মারাত্মক অ্যালার্জি প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "মরিজের কাছে নিজস্ব অটো-ইনজেক্টর থাকলে তা উরুতে প্রয়োগ করতে সাহায্য করুন।",
                "রোগীকে শুইয়ে পা একটু উঁচু করে রাখুন।",
                "পোশাক আলগা করুন।"
            ],
            "next_action": ["শ্বাসপ্রশ্বাস পরীক্ষা করুন।"],
            "what_not_to_do": ["হাঁটতে বা দাঁড়াতে দেবেন না।"],
            "when_to_escalate": "শ্বাস নিতে কষ্ট হলে অবিলম্বে ১১২-তে জানান।"
        },
        "mr": {
            "title": "तीव्र अ‍ॅलर्जी प्रथमोपचार",
            "do_this_now": [
                "मदतीसाठी स्वतःचे इंजेक्शन असल्यास मांडीवर देण्यास मदत करा.",
                "रुग्णाला झोपवून पाय थोडे वर ठेवा.",
                "कपडे सैल करा."
            ],
            "next_action": ["श्वासावर लक्ष ठेवा."],
            "what_not_to_do": ["उभे करू नका किंवा चालवू नका."],
            "when_to_escalate": "घसा सुजल्यास किंवा श्वास अडकल्यास लगेच 112 ला कळवा."
        },
        "ta": {
            "title": "கடுமையான ஒவ்வாமை (Allergy) முதலுதவி",
            "do_this_now": [
                "பயன்படுத்த பரிந்துரைக்கப்பட்ட ஊசி இருந்தால் தொடையில் செலுத்த உதவவும்.",
                "கால்களைச் சற்று உயர்த்திப் படுக்க வைக்கவும்.",
                "ஆடைகளைத் தளர்த்தவும்."
            ],
            "next_action": ["சுவாசத்தைக் கண்காணிக்கவும்."],
            "what_not_to_do": ["நடக்கவோ நிற்கவோ விடாதீர்கள்."],
            "when_to_escalate": "தொண்டை வீங்கினாலோ சுவாசம் தடைபட்டாலோ உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "తీవ్రమైన ఎలర్జీ ప్రథమ చికిత్స",
            "do_this_now": [
                "ఎలర్జీ ఇంజెక్షన్ ఉంటే తొడ భాగంలో ఇవ్వడానికి సహాయపడండి.",
                "కాళ్లను కొద్దిగా పైకి పెట్టి పడుకోబెట్టండి.",
                "దుస్తులను వదులు చేయండి."
            ],
            "next_action": ["శ్వాసను గమనించండి."],
            "what_not_to_do": ["నడవనివ్వవద్దు."],
            "when_to_escalate": "గొంతు వాచినా, శ్వాస ఆడకపోయినా వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "poisoning": {
        "en": {
            "title": "Poisoning / Toxic Substance Exposure First-Aid",
            "do_this_now": [
                "Identify what substance was involved (bottle, pill container, chemical name) if safely possible, to tell the dispatcher and ambulance crew.",
                "If poison is on skin or eyes, flush with clean running water immediately for 15 minutes.",
                "If poison was inhaled, move the person to fresh air immediately.",
                "Keep the person in recovery position on their side if they are drowsy."
            ],
            "next_action": ["Follow the 112 dispatcher's instructions."],
            "what_not_to_do": [
                "DO NOT induce vomiting unless specifically ordered by emergency professionals.",
                "DO NOT give raw eggs, salt water, milk, or home concoctions."
            ],
            "when_to_escalate": "If the person becomes unresponsive, begins seizing, or stops breathing, call 112 immediately."
        },
        "hi": {
            "title": "विषैला पदार्थ / जहर प्राथमिक उपचार",
            "do_this_now": [
                "किस पदार्थ का सेवन हुआ है, उसकी बोतल या डिब्बा संभाल कर रखें ताकि डॉक्टर को दिखा सकें।",
                "यदि जहर त्वचा या आंख पर गिरा हो, तो 15 मिनट तक लगातार पानी से धोएं।",
                "यदि जहरीली गैस सांस में गई हो, तो तुरंत खुली ताजी हवा में ले जाएं।"
            ],
            "next_action": ["112 डिस्पैचर के निर्देशों का पालन करें।"],
            "what_not_to_do": [
                "उल्टी कराने की कोशिश बिल्कुल न करें।",
                "दूध, नमक का पानी या घरेलू नुस्खे न दें।"
            ],
            "when_to_escalate": "बेहोशी या दौरा आने पर तुरंत 112 को बताएं।"
        },
        "bn": {
            "title": "বিষক্রিয়া প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "বিষের বোতল বা উৎস চিহ্নিত করে নিরাপদে রাখুন।",
                "ত্বক বা চোখে লাগলে ১৫ মিনিট পরিষ্কার জলে ধুয়ে নিন।",
                "বিষাক্ত গ্যাস হলে খোলা বাতাসে নিয়ে যান।"
            ],
            "next_action": ["১১২ ডিসপ্যাচারের নির্দেশ পালন করুন।"],
            "what_not_to_do": ["বমি করানোর চেষ্টা করবেন না।", "দুধ বা কাঁচা ডিম খাওয়াবেন না।"],
            "when_to_escalate": "জ্ঞান হারালে সাথে সাথে ১১২-তে জানান।"
        },
        "mr": {
            "title": "विषबाधा प्रथमोपचार",
            "do_this_now": [
                "कोणता विषारी पदार्थ आहे त्याची बाटली किंवा पुडी सांभाळून ठेवा.",
                "डोळ्यात किंवा त्वचेवर पडल्यास १५ मिनिटे पाण्याने धुवा.",
                "मोकळ्या हवेत न्या."
            ],
            "next_action": ["112 च्या सूचना पाळा."],
            "what_not_to_do": ["उलटी करवण्याचा प्रयत्न करू नका.", "दूध किंवा घरगुती उपाय करू नका."],
            "when_to_escalate": "बेशुद्ध पडल्यास त्वरित 112 ला सांगा."
        },
        "ta": {
            "title": "நச்சுப் பாதிப்பு (Poisoning) முதலுதவி",
            "do_this_now": [
                "விஷப் பொருளின் பாட்டிலை மருத்துவக் குழுவிடம் காட்டப் பாதுகாக்கவும்.",
                "கண் அல்லது தோலில் பட்டால் 15 நிமிடங்கள் நீரால் கழுவவும்.",
                "சுத்தமான காற்றுள்ள பகுதிக்குக் கொண்டு செல்லவும்."
            ],
            "next_action": ["அவசர சேவை வழிகாட்டுதலைப் பின்பற்றவும்."],
            "what_not_to_do": ["வாந்தி எடுக்க வைக்க வேண்டாம்.", "பால் அல்லது உப்பு நீர் கொடுக்கக் கூடாது."],
            "when_to_escalate": "மயக்கமடைந்தால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "విషప్రయోగం ప్రథమ చికిత్స",
            "do_this_now": [
                "విష పదార్థపు డబ్బాను వైద్యులకు చూపించడానికి భద్రపరచండి.",
                "కళ్లు లేదా చర్మంపై పడితే 15 నిమిషాలు నీటితో కడగండి.",
                "స్వచ్ఛమైన గాలిలోకి తీసుకెళ్లండి."
            ],
            "next_action": ["డిస్పాచర్ సూచనలను పాటించండి."],
            "what_not_to_do": ["వాంతి చేయించడానికి ప్రయత్నించవద్దు.", "పాలు లేదా ఇతర ద్రవాలు ఇవ్వవద్దు."],
            "when_to_escalate": "స్పృహ కోల్పోతే వెంటనే 112 కు కాల్ చేయండి."
        }
    },
    "general_emergency": {
        "en": {
            "title": "General Emergency First-Aid",
            "do_this_now": [
                "Keep the person in a safe, comfortable position and keep them calm.",
                "Check whether they are conscious and breathing normally.",
                "Follow all direct instructions provided by the emergency dispatcher.",
                "Send someone to the main entrance or road junction to guide the incoming ambulance."
            ],
            "next_action": [
                "Continuously monitor their condition while the ambulance travels to your location.",
                "Keep a blanket handy to keep the patient warm."
            ],
            "what_not_to_do": [
                "DO NOT give food, drink, or medications unless explicitly told by the dispatcher.",
                "DO NOT move the person unnecessarily."
            ],
            "when_to_escalate": "If the condition worsens or the patient becomes unresponsive, call 112 immediately."
        },
        "hi": {
            "title": "सामान्य आपातकालीन प्राथमिक उपचार",
            "do_this_now": [
                "मरीज को सुरक्षित और आरामदायक स्थिति में रखें।",
                "जांचें कि मरीज होश में है और सांस सामान्य चल रही है।",
                "112 डिस्पैचर के निर्देशों का पालन करें।",
                "किसी व्यक्ति को मुख्य सड़क पर एम्बुलेंस को रास्ता दिखाने के लिए भेजें।"
            ],
            "next_action": ["मरीज की स्थिति पर नजर रखें।"],
            "what_not_to_do": ["बिना सलाह कुछ भी खाने या पीने को न दें।", "मरीज को अनावश्यक न हिलाएं।"],
            "when_to_escalate": "स्थिति बिगड़ने पर तुरंत 112 पर दोबारा संपर्क करें।"
        },
        "bn": {
            "title": "সাধারণ জরুরি প্রাথমিক চিকিৎসা",
            "do_this_now": [
                "রোগীকে নিরাপদ ও শান্ত অবস্থানে রাখুন।",
                "জ্ঞান ও শ্বাস স্বাভাবিক আছে কি না লক্ষ্য করুন।",
                "১১২ ডিসপ্যাচারের নির্দেশ মেনে চলুন।",
                "অ্যাম্বুলেন্সকে পথ দেখানোর জন্য কাউকে রাস্তায় পাঠান।"
            ],
            "next_action": ["অনবরত রোগীকে নজরে রাখুন।"],
            "what_not_to_do": ["খাবার বা পানীয় দেবেন না।"],
            "when_to_escalate": "অবস্থার অবনতি হলে ১১২-তে জানান।"
        },
        "mr": {
            "title": "सामान्य आपत्कालीन प्रथमोपचार",
            "do_this_now": [
                "रुग्णाला सुरक्षित व आरामदायी स्थितीत ठेवा.",
                "श्वास आणि शुद्ध तपासा.",
                "112 च्या सूचना पाळा.",
                "अ‍ॅम्ब्युलन्सला रस्ता दाखवण्यासाठी कोणालातरी मुख्य रस्त्यावर पाठवा."
            ],
            "next_action": ["रुग्णावर सतत लक्ष ठेवा."],
            "what_not_to_do": ["खाण्यास किंवा पिण्यास देऊ नका."],
            "when_to_escalate": "तब्बेत बिघडल्यास लगेच 112 ला कळवा."
        },
        "ta": {
            "title": "பொதுவான அவசர முதலுதவி",
            "do_this_now": [
                "பாதிக்கப்பட்டவரைப் பாதுகாப்பான நிலையில் வைக்கவும்.",
                "சுவாசம் சீராக உள்ளதா எனப் பார்க்கவும்.",
                "112 வழிகாட்டுதலைப் பின்பற்றவும்.",
                "ஆம்புலன்ஸை வழிநடத்த ஒருவரை முதன்மைச் சாலைக்கு அனுப்பவும்."
            ],
            "next_action": ["பாதிக்கப்பட்டவரை அமைதியாகக் கண்காணிக்கவும்."],
            "what_not_to_do": ["உணவு அல்லது தண்ணீர் கொடுக்க வேண்டாம்."],
            "when_to_escalate": "நிலைமை மோசமடைந்தால் உடனே 112 ஐ அழைக்கவும்."
        },
        "te": {
            "title": "సాధారణ అత్యవసర ప్రథమ చికిత్స",
            "do_this_now": [
                "బాధితుడిని సురక్షితమైన స్థానంలో ఉంచండి.",
                "శ్వాసను పరిశీలించండి.",
                "112 సూచనలను పాటించండి.",
                "అంబులెన్స్‌కు దారి చూపించడానికి ఒకరిని రోడ్డుపైకి పంపండి."
            ],
            "next_action": ["పరిస్థితిని నిరంతరం గమనించండి."],
            "what_not_to_do": ["ఆహారం లేదా నీరు ఇవ్వవద్దు."],
            "when_to_escalate": "పరిస్థితి విషమిస్తే వెంటనే 112 కు కాల్ చేయండి."
        }
    }
}

# ==============================================================================
# KEYWORD MATCHING ENGINE (Multi-lingual & Prioritized)
# ==============================================================================
CATEGORY_PATTERNS = {
    "pregnancy_emergency": [
        r"pregnant", r"pregnancy", r"delivery", r"water broke", r"labor", r"गर्भवती", r"प्रसव",
        r"গর্ভবতী", r"গরোদর", r"கர்ப்பிணி", r"గర్భిణీ", r"ప్రసవం"
    ],
    "child_emergency": [
        r"child", r"baby", r"infant", r"toddler", r"kid", r"बच्चा", r"शिशु", r"বাচ্চা",
        r"लहान मूल", r"குழந்தை", r"పాప", r"బాబు"
    ],
    "choking": [
        r"chok", r"food stuck", r"throat stuck", r"swallowed", r"গলায়", r"गले में", r"अटक",
        r"घशात", r"தொண்டை", r"அடைப்பு", r"గొంతు", r"అడ్డు"
    ],
    "cardiac_emergency": [
        r"chest pain", r"heart attack", r"cardiac", r"सीने में दर्द", r"हार्ट अटैक", r"দিল",
        r"বুকে ব্যথা", r"हृदयविकार", r"छातीत", r"நெஞ்சு வலி", r"இதயம்", r"గుండె నొప్పి"
    ],
    "stroke_symptoms": [
        r"stroke", r"paralysis", r"face droop", r"arm weak", r"slurred speech", r"लकवा", r"पक्षाघात",
        r"স্ট্রোক", r"பக்கவாதம்", r"పక్షవాతం"
    ],
    "unconsciousness": [
        r"unconscious", r"faint", r"passed out", r"not waking", r"not breathing", r"unresponsive",
        r"बेहोश", r"होश", r"অজ্ঞান", r"নিস্তেজ", r"बेशुद्ध", r"शुद्ध", r"மயக்கம்", r"స్పృహ"
    ],
    "breathing_difficulty": [
        r"breath", r"shortness", r"asthma", r"wheezing", r"gasping", r"सांस", r"दमा",
        r"শ্বাসকষ্ট", r"श्वास", r"धाप", r"மூச்சு", r"திணறல்", r"శ్వాస"
    ],
    "severe_bleeding": [
        r"bleed", r"blood", r"hemorrhage", r"कट गया", r"खून", r"रक्त", r"रक्तपात", r"রক্ত", r"রক্তক্ষরণ",
        r"रक्तस्त्राव", r"இரத்தம்", r"ரத்தப்போக்கு", r"రక్తం", r"రక్తస్రావం", r"spurting"
    ],
    "road_accident": [
        r"accident", r"crash", r"collision", r"run over", r"hit by", r"दुर्घटना", r"एक्सिडेंट",
        r"টক্কর", r"अपघात", r"விபத்து", r"ప్రమాదం"
    ],
    "burns": [
        r"burn", r"fire", r"scald", r"acid", r"जल", r"आग", r"পুড়ে", r"भाजले", r"தீக்காயம்", r"కాలిన"
    ],
    "seizure": [
        r"seizure", r"convulsion", r"fits", r"epilepsy", r"दौरा", r"मिर्गी", r"খিঁচুনি", r"फेफरे",
        r"வலிப்பு", r"ఫిట్స్", r"మూర్ఛ"
    ],
    "fracture_injury": [
        r"fracture", r"broken bone", r"sprain", r"dislocat", r"हड्डी टूट", r"হাড় ভাঙা",
        r"हाड मोडणे", r"எலும்பு முறிவு", r"ఎముక విరిగింది"
    ],
    "poisoning": [
        r"poison", r"swallowed chemical", r"pesticide", r"overdose", r"जहर", r"विष", r"বিষ",
        r"विषबाधा", r"நஞ்சு", r"విషం"
    ],
    "allergic_reaction": [
        r"allergy", r"allergic", r"anaphylaxis", r"swollen face", r"hives", r"एलर्जी", r"অ্যালার্জি",
        r"ஒவ்வாமை", r"ఎలర్జీ"
    ]
}

# Prompt injection patterns
PROMPT_INJECTION_PATTERNS = [
    r"ignore\b.*?\b(rules|instructions|prompts|guidelines|safety)",
    r"disregard\b.*?\b(rules|instructions|system|safety)",
    r"reveal\b.*?\b(prompt|instructions|secret|api key)",
    r"dangerous procedure",
    r"give me a dangerous",
    r"pretend you are",
    r"jailbreak",
    r"override medical",
    r"bypass safety",
]


def detect_prompt_injection(text: str) -> bool:
    """Detect attempts to override safety rules or extract prompts."""
    text_lower = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def classify_emergency_category(text: str) -> str:
    """Classify user text into one of the controlled emergency categories."""
    text_lower = text.lower()
    for category, patterns in CATEGORY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                return category
    return "general_emergency"


def get_approved_guidance(category: str, lang: str = "en") -> dict[str, Any]:
    """Retrieve verified medical guidance for a category and language."""
    cat = category if category in APPROVED_PROTOCOLS else "general_emergency"
    lang_code = lang if lang in ("en", "hi", "bn", "mr", "ta", "te") else "en"
    
    guidance = APPROVED_PROTOCOLS.get(cat, {}).get(lang_code)
    if not guidance:
        guidance = APPROVED_PROTOCOLS.get(cat, {}).get("en")
    if not guidance:
        guidance = APPROVED_PROTOCOLS["general_emergency"]["en"]
    return guidance


def validate_safety_output(output_text: str, category: str) -> tuple[bool, str]:
    """
    Validates assistant output to enforce strict clinical safety:
    1. NEVER claims a definitive diagnosis.
    2. NEVER prescribes prescription medication.
    3. NEVER tells patient to cancel emergency services.
    """
    lower = output_text.lower()
    
    # Check for diagnosis claim
    if any(phrase in lower for phrase in ["i diagnose", "you have a definitive", "confirmed diagnosis", "you definitely have"]):
        return False, "DIAGNOSIS_CLAIM_REJECTED"
        
    # Check for medication prescription
    if any(phrase in lower for phrase in ["take 500mg", "take prescription", "take aspirin right away", "drink medication"]):
        return False, "MEDICATION_PRESCRIPTION_REJECTED"
        
    # Check for cancellation of emergency
    if any(phrase in lower for phrase in ["cancel the ambulance", "ambulance is not needed", "cancel emergency request", "don't call 112"]):
        return False, "AMBULANCE_CANCELLATION_REJECTED"
        
    return True, "SAFE"


async def process_emergency_message(
    db: Session,
    session: EmergencyAssistantSession,
    user_message: str,
    user: User,
    ai_provider: Any = None
) -> dict[str, Any]:
    """
    Retrieval + Safety Rules + Response Generation pipeline:
    1. Ingestion & Sanitization.
    2. Prompt Injection Defense.
    3. Category Classification.
    4. Approved Knowledge Retrieval.
    5. AI-Assisted Formulation with Fallback.
    6. Strict Output Safety Validation.
    7. Database Logging (Session, Message, SafetyEvents).
    """
    clean_message = user_message.strip()
    language = session.language or user.language or "en"
    
    # 1. Log incoming user message
    db.add(EmergencyAssistantMessage(
        session_id=session.id,
        sender="user",
        message=clean_message,
        category=None,
        structured_data={}
    ))
    db.flush()

    # 2. Check for Prompt Injection Defense
    if detect_prompt_injection(clean_message):
        safety_event = EmergencySafetyEvent(
            emergency_id=session.emergency_request_id,
            category="safety_override",
            severity="CRITICAL",
            action="PROMPT_INJECTION_DEFENDED",
            detail={"user_input": clean_message[:200]}
        )
        db.add(safety_event)
        
        # Override with safe guidance
        guidance = get_approved_guidance("general_emergency", language)
        response_text = (
            f"⚠️ Emergency safety notice: MediRoute Emergency Assistant provides verified first-aid guidance only. "
            f"If an emergency is occurring, please follow your emergency dispatcher's instructions and call {EMERGENCY_PHONE_NUMBER} immediately."
        )
        assistant_msg = EmergencyAssistantMessage(
            session_id=session.id,
            sender="assistant",
            message=response_text,
            category="general_emergency",
            structured_data={
                "safety_override": True,
                "title": guidance["title"],
                "do_this_now": guidance["do_this_now"],
                "next_action": guidance["next_action"],
                "what_not_to_do": guidance["what_not_to_do"],
                "when_to_escalate": guidance["when_to_escalate"],
                "emergency_phone": EMERGENCY_PHONE_NUMBER
            }
        )
        db.add(assistant_msg)
        db.commit()
        db.refresh(assistant_msg)
        return {
            "message_id": assistant_msg.id,
            "session_id": session.id,
            "sender": "assistant",
            "message": response_text,
            "category": "general_emergency",
            "structured_data": assistant_msg.structured_data,
            "source": "Approved Emergency First-Aid Protocols",
            "emergency_phone": EMERGENCY_PHONE_NUMBER,
            "timestamp": assistant_msg.timestamp.isoformat()
        }

    # 3. Classify Category
    category = classify_emergency_category(clean_message)
    
    # If life threatening, log high severity safety event
    if category in ("unconsciousness", "cardiac_emergency", "severe_bleeding", "choking"):
        db.add(EmergencySafetyEvent(
            emergency_id=session.emergency_request_id,
            category=category,
            severity="CRITICAL",
            action="CRITICAL_CATEGORY_DETECTED",
            detail={"user_message": clean_message[:200]}
        ))

    # 4. Retrieve Approved Guidance
    guidance = get_approved_guidance(category, language)
    
    # 5. AI Synthesis or Deterministic Fallback
    formatted_steps = f"### {guidance['title']}\n\n"
    formatted_steps += "**🚨 DO THIS NOW:**\n"
    for i, step in enumerate(guidance['do_this_now'], 1):
        formatted_steps += f"{i}. {step}\n"
    formatted_steps += "\n**⏳ NEXT ACTION:**\n"
    for step in guidance['next_action']:
        formatted_steps += f"• {step}\n"
    formatted_steps += "\n**⚠️ WHAT NOT TO DO:**\n"
    for caution in guidance['what_not_to_do']:
        formatted_steps += f"• {caution}\n"
    formatted_steps += f"\n**📞 WHEN TO ESCALATE:**\n{guidance['when_to_escalate']}\n"

    final_message_text = formatted_steps
    is_ai_generated = False
    
    if ai_provider and hasattr(ai_provider, "analyse"):
        try:
            prompt_context = (
                f"You are the MediRoute Emergency First-Aid Assistant. An ambulance is on the way. "
                f"Explain these approved instructions clearly and calmly: {guidance['title']}. "
                f"Do not invent medical facts. Do not diagnose. User input: {clean_message}"
            )
            ai_res = await ai_provider.analyse("emergency_first_aid", prompt_context)
            if ai_res and ai_res.get("ai_assisted") and "result" in ai_res:
                candidate_text = ai_res["result"].get("analysis", "")
                is_safe, reason = validate_safety_output(candidate_text, category)
                if is_safe and len(candidate_text) > 30:
                    final_message_text = f"{candidate_text}\n\n{formatted_steps}"
                    is_ai_generated = True
                else:
                    db.add(EmergencySafetyEvent(
                        emergency_id=session.emergency_request_id,
                        category=category,
                        severity="WARNING",
                        action="AI_OUTPUT_SAFETY_OVERRIDE",
                        detail={"reason": reason}
                    ))
        except Exception:
            pass

    # 6. Save assistant message to database
    structured_payload = {
        "title": guidance["title"],
        "do_this_now": guidance["do_this_now"],
        "next_action": guidance["next_action"],
        "what_not_to_do": guidance["what_not_to_do"],
        "when_to_escalate": guidance["when_to_escalate"],
        "emergency_phone": EMERGENCY_PHONE_NUMBER,
        "is_ai_assisted": is_ai_generated,
        "source": "WHO / Red Cross Approved First-Aid Protocols"
    }

    assistant_msg = EmergencyAssistantMessage(
        session_id=session.id,
        sender="assistant",
        message=final_message_text,
        category=category,
        structured_data=structured_payload
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return {
        "message_id": assistant_msg.id,
        "session_id": session.id,
        "sender": "assistant",
        "message": final_message_text,
        "category": category,
        "structured_data": structured_payload,
        "source": "WHO / Red Cross Approved First-Aid Protocols",
        "emergency_phone": EMERGENCY_PHONE_NUMBER,
        "timestamp": assistant_msg.timestamp.isoformat()
    }

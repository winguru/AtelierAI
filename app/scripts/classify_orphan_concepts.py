#!/usr/bin/env python3
"""Classify orphan root concepts into super-category parents.

Creates ~15 super-category parent concepts, then keyword-matches ~6,890 orphan
root concepts and assigns them a parent_concept_id.

Default mode is dry-run (prints stats, writes no data). Use --apply to persist.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: import path setup

from backend.database import SessionLocal
from backend.models import Concept


# ---------------------------------------------------------------------------
# 11 existing organized tree roots — MUST NOT be reclassified
# ---------------------------------------------------------------------------
EXISTING_TREE_ROOT_IDS: set[int] = {
    1174,   # franchise/media
    1332,   # clothing
    1970,   # expression
    2210,   # focus
    3160,   # image style
    3577,   # location
    4767,   # nsfw rating
    5341,   # sexuality
    5943,   # subject (entities)
    6151,   # (thematic)
    6985,   # text balloon (emoticons)
    # Super-category parents (created 2026-06-11)
    7236,   # anatomy & body
    7237,   # hair & head
    7238,   # facial expression & emotion
    7239,   # clothing & fashion
    7240,   # actions & poses
    7241,   # objects & props
    7242,   # creatures & species
    7243,   # setting & background
    7244,   # franchise & fandom
    7245,   # art style & technique
    7246,   # seasons & events
    7247,   # text & language
    7248,   # lighting & effects
    7249,   # composition & format
    7250,   # relationships & demographics
}


# ---------------------------------------------------------------------------
# Super-category definitions
# ---------------------------------------------------------------------------
@dataclass
class SuperCategory:
    name: str
    description: str
    exact: set[str] = field(default_factory=set)
    prefixes: list[str] = field(default_factory=list)
    suffixes: list[str] = field(default_factory=list)
    contains: list[str] = field(default_factory=list)
    regex_patterns: list[str] = field(default_factory=list)
    # Set at runtime after DB insert
    concept_id: int | None = None


CATEGORIES: list[SuperCategory] = [
    # 1. anatomy & body
    SuperCategory(
        name="anatomy & body",
        description="Body parts, body states, body modifications, physique",
        exact={
            "abs", "areolae", "back", "belly", "blood", "body", "bones",
            "breasts", "butt", "calves", "chest", "clavicle", "collarbone",
            "ear", "ears", "elbow", "face", "fingernails", "fingers", "foot",
            "forearm", "forehead", "genitals", "groin", "hands", "head",
            "heel", "hips", "jaw", "knee", "legs", "lip", "lips", "midriff",
            "mouth", "muscle", "muscles", "muscular", "nail", "nails",
            "navel", "neck", "nipple", "nipples", "nose", "pelvis",
            "ribcage", "scar", "scars", "shoulder", "shoulders", "skin",
            "spine", "stomach", "teeth", "thigh", "thighs", "throat",
            "toe", "toes", "tongue", "torso", "veins", "waist", "wrist",
            "wing", "wings", "tail", "tails", "horn", "horns",
            "tattoo", "tan", "tanlines", "mole", "moles", "freckckles",
            "freckles", "birthmark", "navel piercing", "muscle definition",
            "pubic hair", "veiny", "vascular", "cleavage", "sideboob",
            "underboob", "puffy sleeves",
        },
        prefixes=[
            "arm ", "leg ", "breast ", "body ", "facial ", "hand ",
            "bare ", "muscular ", "long ", "short ", "wide ",
            "large ", "small ", "huge ", "giant ", "tiny ",
        ],
        suffixes=[
            " body", " muscles", " tattoo", " scar", " mole",
            " piercing", " tan", " lines", " wings", " horns",
            " tail", " nipples", " navel",
        ],
        contains=[
            "breast", "muscle", "body", "cheek", "mouth",
            "pupil", "navel", "armpit", "skin", "teeth",
            "eyebrow", "lips", "tongue", "penis", "thigh",
            "vagina", "testicle", "anus", "nails", "bone",
            "amputee", "pussy", "ass", "nipple",
            "crotch", "areola", "bald", "beard",
            "biceps", "blood", "bruise", "big",
        ],
        regex_patterns=[
            r"^\d+ ?(boys?|girls?|men|women|males?|females?)",  # "1boy", "2girls"
            r"^\d\+ ?(boys?|girls?|others?)",                   # "6+boys", "6+others"
            r"^(long |short |medium )?(hair|bangs)",             # hair is anatomy-adjacent
            # body part as final word (color/adj + body part)
            r"(wings|horns|toes|fingers|waist|hips|chest|navel|forehead|jaw)$",
            # "on X" body part focus patterns
            r"(on\s+)?(hand|hands?|arm|arms?|leg|legs?|head|neck|back|face|shoulder|foot|feet|knee|elbow)$",
        ],
    ),
    # 2. hair & head
    SuperCategory(
        name="hair & head",
        description="Hairstyles, hair colors, hair accessories, head features",
        exact={
            "ahoge", "antenna hair", "asymmetric hair", "bangs", "blunt bangs",
            "bob cut", "braided bangs", "braid", "braids", "curly hair",
            "curtain bangs", "diagonal bangs", "dreadlocks", "dual braids",
            "dyed hair", "flipped hair", "hair between eyes", "hair flower",
            "hair ornament", "hair over one eye", "hairpin", "hair over shoulder",
            "hair ring", "hair scrunchie", "hair tie", "hime cut", "messy hair",
            "micro braids", "midriff", "mohawk", "multicolored hair",
            "one side up", "parted bangs", "pixie cut", "ponytail",
            "ringlets", "sailor collar", "side ponytail", "sidelocks",
            "short hair", "swept back hair", "twin braids", "twintails",
            "wavy hair", "wet hair", "hair", "hairbrush",
            "black hair", "blonde hair", "blue hair", "brown hair",
            "grey hair", "pink hair", "purple hair", "red hair",
            "silver hair", "white hair", "green hair", "orange hair",
            "gradient hair", "streaked hair", "two-tone hair",
            "hair bun", "single hair bun", "double bun", "cone hair bun",
            "detached sleeves", "hair pulled back",
        },
        prefixes=[
            "hair ", "blonde ", "blue ", "pink ", "purple ", "red ",
            "silver ", "white ", "green ", "orange ", "grey ",
        ],
        suffixes=[
            " hair", " bangs", " braid", " bun", " ponytail",
            " twintails", " sidelocks", " ahoge",
        ],
        contains=["hair", "bangs", "sidelocks", "ahoge", "twintails", "ponytail", "braid"],
        regex_patterns=[
            r"hair(?!s?$)",                   # hairX patterns (hairs excluded — kept in anatomy)
            r"(black|blonde|blue|brown|grey|pink|purple|red|silver|white|green|orange)\s+hair$",
        ],
    ),
    # 3. facial expression & emotion
    SuperCategory(
        name="facial expression & emotion",
        description="Expressions, eye states, emotional states, emotional reactions",
        exact={
            "blush", "blushing", "crying", "emotionless", "frown", "grimace",
            "grin", "happy", "laughing", "nervous", "sad", "scared",
            "shy", "smile", "smirk", "teary-eyed", "tears", "worried",
            "angry", "annoyed", "bored", "confused", "curious", "determined",
            "embarrassed", "excited", "fearful", "frustrated", "guilty",
            "hopeful", "jealous", "lonely", "love", "mischievous", "nervous",
            "panic", "peaceful", "pensive", "proud", "relieved", "reluctant",
            "serious", "shocked", "sleepy", "surprised", "thinking",
            "tired", "unhappy", "wide-eyed",
            "closed eyes", "closed mouth", "half-closed eyes", "one eye closed",
            "open mouth", "open eyes", "clenched teeth", "biting lip",
            "looking away", "looking at viewer", "looking up", "looking down",
            "looking back", "looking to the side", "staring", "side eye",
            "expressionless", "bored expression",
        },
        prefixes=["expression ", "face ", "eye "],
        suffixes=[
            " smile", " blush", " grin", " frown", " expression",
            " eyes", " look",
        ],
        contains=["blush", "expression", "looking", "tears"],
        regex_patterns=[
            r"looking\s+(at|away|back|down|up|to\s+the\s+side)",
        ],
    ),
    # 4. clothing & fashion
    SuperCategory(
        name="clothing & fashion",
        description="Garments, footwear, accessories, color variants of clothing",
        exact={
            "apron", "bikini", "blouse", "bodysuit", "boots", "bowtie",
            "bracelet", "bra", "button-down", "camisole", "cape", "cardigan",
            "choker", "coat", "corset", "crown", "cufflinks", "dress",
            "earrings", "formal wear", "garter belt", "garters", "gloves",
            "gown", "hat", "headband", "headphones", "headwear", "heels",
            "hoodie", "jacket", "jeans", "jewelry", "jumper", "kimono",
            "leotard", "lingerie", "mask", "miniskirt", "necklace", "necktie",
            "nightgown", "one-piece swimsuit", "overalls", "overcoat",
            "panties", "pants", "pantyhose", "ribbon", "ring", "robe",
            "sailor dress", "sandals", "sash", "scarf", "shirt", "shoes",
            "shorts", "skirt", "sleeveless", "sleeves", "slippers",
            "sneakers", "socks", "stockings", "suit", "sundress",
            "sunglasses", "suspenders", "sweater", "swimsuit", "t-shirt",
            "thighhighs", "tights", "top", "tophat", "trench coat",
            "underwear", "uniform", "vest", "wings", "wristband",
            "thong", "bloomers", "buruma", "cheerleader uniform",
            "maid dress", "maid apron", "school uniform",
            "sailor collar", "serafuku", "thigh boots", "knee boots",
            "ankle boots", "high heels", "stiletto heels",
            "frilled dress", "backless dress", "black dress",
            "white dress", "long dress", "short dress",
            "micro skirt", "pencil skirt", "denim skirt",
            "sleeveless dress", "hoodie", "crop top", "off shoulder",
            "shoulder cutout", "side slit", "open clothes",
            "clothes pull", "clothes aside", "between breasts",
        },
        prefixes=[
            "black ", "white ", "red ", "blue ", "pink ", "green ",
            "purple ", "yellow ", "orange ", "brown ", "grey ", "silver ",
            "gold ", "dark ", "light ", "long ", "short ",
        ],
        suffixes=[
            " dress", " skirt", " shirt", " shorts", " boots", " bikini",
            " jacket", " coat", " sweater", " top", " gloves", " hat",
            " scarf", " stockings", " thighhighs", " heels",
            " panties", " legwear", " bra", " costume", " headwear",
            " ribbon", " sleeves", " mask", " hood", " cape",
            " necklace", " choker", " collar", " belt", " outfit",
            " earrings", " kimono", " leotard", " underwear",
            " pantyhose", " corset", " bodysuit", " vest",
            " blouse", " poncho", " ring",
            " eyewear", " cap", " socks", " strap",
            " necktie", " bowtie", " band", " wrap",
        ],
        contains=[
            "dress", "skirt", "bikini", "shirt", "jacket", "pants",
            "shorts", "swimsuit", "lingerie", "uniform", "boots",
            "stockings", "thighhighs", "footwear", "panties",
            "legwear", "costume", "headwear", "ribbon", "sleeves",
            "cutout", "necklace", "choker", "collar", "earrings",
            "kimono", "leotard", "bodysuit", "corset", "outfit",
            "clothes", "underwear", "eyewear",
            "socks", "cap", "strap", "necktie",
            "bowtie", "suit", "headband", "print",
            "armor", "helmet", "cuffs", "hoodie",
            "bracelet", "crown", "pouch", "brooch",
            "cloak", "sarashi", "towel", "skates",
            "trim", "apron", "button",
            "bandana", "bandeau", "beret", "blazer",
            "blindfold", "bodice", "briefs", "bride",
            "bridal", "blonde", "bodysuit",
            "boot", "bottomless", "bralines",
            "boobplate", "blouse",
            "accessories", "ascot", "aiguillette",
            "adidas", "army", "ascot",
        ],
        regex_patterns=[
            # color + clothing item
            r"(black|white|red|blue|pink|green|purple|yellow|"
            r"orange|brown|grey|silver|gold|dark|light)\s+"
            r"(dress|skirt|shirt|shorts|boots|bikini|jacket|"
            r"coat|sweater|top|gloves|hat|scarf|stockings|"
            r"thighhighs|heels|shoes|underwear|panties|bra|"
            r"swimsuit|leotard|apron|coat|robe|gown)",
            # modifier + clothing item
            r"(long|short|sleeveless|open|closed|frilled|"
            r"backless|crop|off)\s+"
            r"(dress|skirt|sleeve|top|clothes|shirt)",
        ],
    ),
    # 5. actions & poses
    SuperCategory(
        name="actions & poses",
        description="Body positions, interactions, combat stances, holding objects",
        exact={
            "standing", "sitting", "lying", "kneeling", "crouching",
            "walking", "running", "jumping", "falling", "flying",
            "dancing", "fighting", "sleeping", "waking", "stretching",
            "bending", "leaning", "reaching", "grabbing", "holding",
            "pulling", "pushing", "throwing", "catching", "kicking",
            "punching", "hugging", "kissing", "pointing", "waving",
            "clapping", "crossed arms", "arms behind back", "arms up",
            "arms at sides", "hand on hip", "hand in pocket",
            "hands clasped", "hands behind head", "hands on hips",
            "hands up", "legs together", "legs apart", "legs crossed",
            "legs spread", "on back", "on stomach", "on side",
            "all fours", "against wall", "bent over", "spread arms",
            "spread legs", "squatting", "head tilt", "looking back",
            "lying on back", "lying on stomach", "lying on side",
            "sitting on", "straddling", "carrying", "riding",
            "swinging", "biting", "drinking", "eating", "reading",
            "writing", "singing", "playing", "touching", "petting",
            "feeding", "tying", "adjusting", "removing", "fixing",
            "tying hair", "adjusting clothes",
        },
        prefixes=[
            "holding ", "carrying ", "wielding ", "hand on ",
            "arm ", "leg ", "foot ", "head ",
        ],
        suffixes=[
            " pose", " grab", " support", " hold", " carry",
            " stance", " position", " on back", " on stomach",
            " on side", " from behind", " from above", " from below",
            " from side",
        ],
        contains=[
            "standing", "sitting", "lying", "kneeling", "walking",
            "running", "jumping", "dancing", "fighting", "pose",
            "grab", "pull", "lift", "spread", "remov", "adjust",
            "support", "hug", "sex", "kiss", "gag", "censor",
            "penetration", "masturbation", "nudity", "cover",
            "fellatio", "paizuri", "handjob", "anal", "threesome",
            "ejaculation", "ahegao", "cum", "oral", "bondage",
            "rape", "biting", "peek", "bathing", "riding",
            "up", "down", "out", "together",
            "bdsm", "asphyxiation", "abuse", "anilingus",
            "bestiality", "aroused", "battle",
            "bound", "blow", "blowing", "blowjob",
            "breathing", "beckoning", "balancing",
            "broken", "bleeding", "blind",
            "borrowed", "bruise",
            "archery", "aiming", "afloat",
            "animated", "animation", "accidental",
        ],
        regex_patterns=[
            r"^(sitting|standing|lying|kneeling|walking|running|jumping|flying|dancing|fighting|sleeping|crouching|squatting|leaning|bending)\b",
            r"(from\s+behind|from\s+above|from\s+below|from\s+side)",
            r"^(arm|leg|hand|foot|head)s?\s+(up|down|crossed|behind|on|at|together|apart|spread)",
            r"\bpose\b",
        ],
    ),
    # 6. objects & props
    SuperCategory(
        name="objects & props",
        description="Weapons, nature objects, food, vehicles, instruments, tools",
        exact={
            "sword", "gun", "shield", "bow", "arrow", "staff", "wand",
            "axe", "spear", "dagger", "knife", "katana", "hammer",
            "weapon", "weapons", "armor", "book", "books", "candle",
            "camera", "cell phone", "chair", "clock", "cup", "drum",
            "fan", "flag", "flower", "flowers", "food", "guitar",
            "key", "keys", "lamp", "laptop", "microphone", "mirror",
            "paper", "pen", "pencil", "phone", "pillow", "plate",
            "potted plant", "purse", "scroll", "smartphone", "staff",
            "staff of asclepius", "stool", "table", "tablet",
            "umbrella", "vase", "violin", "water bottle", "whip",
            "ball", "balloon", "bat", "bottle", "box", "bucket",
            "cane", "card", "cards", "chain", "coin", "crate",
            "crown", "cup", "frame", "glass", "glasses", "globe",
            "hook", "horn", "jar", "jug", "kite", "knife",
            "ladder", "leaf", "leaves", "log", "magnifying glass",
            "mask", "mug", "net", "pan", "pot", "ribbon",
            "rope", "sack", "scale", "scissors", "scroll", "shell",
            "skull", "spade", "stone", "ticket", "tissue", "tool",
            "torch", "treasure", "trumpet", "trunk", "tube",
            "weapon", "whip", "wrench", "branch", "twig",
            "food", "fruit", "apple", "banana", "cake", "candy",
            "chocolate", "cookie", "donut", "grape", "ice cream",
            "lemon", "melon", "orange", "pear", "pizza", "sandwich",
            "steak", "strawberry", "sushi", "watermelon",
            "vehicle", "car", "bicycle", "bike", "bus", "train",
            "airplane", "boat", "ship", "motorcycle", "scooter",
            "skateboard", "surfboard", "snowboard", "skis",
            "balloon", "kite", "rocket", "spaceship",
            "basket", "backpack", "bag", "briefcase", "luggage",
            "suitcase", "school bag",
        },
        prefixes=[
            "holding ", "wielding ", "carrying ", "with ",
        ],
        suffixes=[
            " sword", " gun", " bow", " shield", " staff", " wand",
            " book", " flower", " food", " drink", " fruit",
        ],
        contains=[
            "weapon", "food", "flower", "fruit", "instrument",
            "backpack", "balloon", "bottle", "candle", "flag",
            "symbol", "ornament", "mark", "rifle", "ball",
            "bag", "box", "umbrella", "glass", "table",
            "pillow", "aircraft", "guitar", "clock", "fan",
            "chair", "bed", "couch", "piano", "drum",
            "bell", "bowl", "bar", "bench", "fence",
            "machine", "vehicle", "doll", "food", "can",
            "cup", "cube", "phone", "controller", "door",
            "condom", "game", "note", "moon", "cross",
            "desk", "spikes", "slice", "sword",
            "blade", "baton", "banner", "badge", "bead",
            "beans", "beer", "bento", "belt", "binoculars",
            "blanket", "bottle", "bow", "bucket",
            "cane", "card", "chain", "coin",
            "crystal", "flag", "gem", "gun",
            "hammer", "jar", "jug", "key",
            "knife", "ladder", "lamp", "lantern",
            "map", "microphone", "mirror", "net",
            "pipe", "pot", "ribbon", "rope",
            "scroll", "shield", "staff", "stool",
            "ticket", "tool", "torch", "tray",
            "trophy", "tube", "vase", "wand",
            "whip", "wire",
            "bamboo", "bandolier", "beanie", "bouquet",
            "bomb", "bread", "brick", "book",
            "bokken", "bracer", "bolo",
            "bot", "brooch", "bubbles",
            "cone", "crate", "dowel",
            "herb", "leaf",
            "mushroom", "plant", "seed",
            "stone", "twig",
            "ammunition", "amplifier", "amulet", "anchor",
            "anklet", "armband", "armlet", "antenna",
            "arcade", "arch", "acorn", "arrow",
            "altar", "ankh", "aqua", "armor",
            "arrow", "axe", "bag",
            "barrier", "battery", "beacon",
            "bloomer", "book", "boomerang",
            "broom", "brush", "cage",
            "calender", "camera", "candelabra",
            "canvas", "cap", "carriage",
            "cauldron", "ceiling", "chain",
            "chalk", "champagne", "compass",
            "container", "cosmic", "costume",
            "crate", "crossbow", "curtain",
            "diamond", "diary", "disc",
            "display", "draws", "dumbbell",
            "emblem", "engine", "envelope",
            "erruption", "face", "fan",
            "figure", "flask", "fork",
            "frame", "gallery", "game",
            "globe", "grab", "grail",
            "grimoire", "grip", "head",
            "heart", "holy", "ice",
            "icon", "idol", "instrument",
            "jewel", "label", "letter",
            "lock", "logo", "mark",
            "mask", "materia", "meal",
            "medal", "medicine", "metal",
            "monocle", "neck", "necklace",
            "noise", "object", "organ",
            "paint", "palette", "pan",
            "parasol", "perfume", "photo",
            "pill", "platter", "quiver",
            "rattle", "record", "rocket",
            "sack", "scepter", "scope",
            "screen", "sculpture", "seal",
            "sensu", "shaker", "sheath",
            "shrine", "sling", "souvenir",
            "spear", "spin", "stamp",
            "statue", "stave", "sticker",
            "stopwatch", "strap", "string",
            "suit", "symbol", "talisman",
            "tamakushi", "target", "thread",
            "throne", "tiara", "token",
            "tomoe", "top", "trap",
            "trinket", "umbrella", "urn",
            "veneer", "weight", "wheel",
        ],
        regex_patterns=[
            r"^(holding|carrying|wielding|with)\s+(a\s+)?",
        ],
    ),
    # 7. creatures & species
    SuperCategory(
        name="creatures & species",
        description="Mythological beings, hybrids, fantasy races, specific animals",
        exact={
            "alien", "angel", "animal", "animals", "bat", "bear", "bee",
            "bird", "butterfly", "cat", "centaur", "dragon", "dwarf",
            "elf", "fairy", "fish", "fox", "frog", "ghost", "goblin",
            "golem", "harpy", "horse", "insect", "kemonomimi",
            "kitsune", "lizard", "mermaid", "monkey", "monster",
            "nymph", "orc", "phoenix", "rabbit", "robot", "scorpion",
            "skeleton", "slime", "snake", "spider", "squirrel",
            "succubus", "tiger", "unicorn", "vampire", "werewolf",
            "wolf", "zombie", "demon", "deva", "giant", "gnome",
            "griffin", "hydra", "imp", "minotaur", "ogre", "pegasus",
            "satyr", "troll", "wendigo", "wraith", "wyvern",
            "cat ears", "dog ears", "fox ears", "rabbit ears",
            "cat tail", "fox tail", "horse ears", "wolf ears",
            "catgirl", "foxgirl", "bunnygirl", "wolfgirl",
            "doggirl", "cowgirl", "dragon girl",
            "dinosaur", "penguin", "whale", "dolphin", "shark",
            "octopus", "squid", "crab", "lobster", "turtle",
            "tortoise", "crocodile", "alligator", "eagle", "hawk",
            "owl", "parrot", "crow", "raven", "swan", "deer",
            "moose", "elk", "gorilla", "chimp", "panda", "koala",
            "kangaroo", "giraffe", "elephant", "lion", "leopard",
            "panther", "cheetah", "hyena", "hippo", "rhino",
            "zebra", "buffalo", "bison", "camel", "llama",
            "sheep", "goat", "pig", "cow", "donkey", "mule",
            "chicken", "rooster", "duck", "goose", "turkey",
            "peacock", "flamingo", "hamster", "guinea pig",
            "mouse", "rat", "hedgehog", "otter", "beaver",
            "raccoon", "skunk", "sloth", "ant", "beetle",
            "ladybug", "mosquito", "fly", "wasp", "dragonfly",
            "firefly", "caterpillar", "snail", "slug", "worm",
            "jellyfish", "starfish", "seahorse", "coral",
            "mushroom", "plant", "tree",
        },
        prefixes=["animal ", "mythical ", "fantasy "],
        suffixes=[
            " ears", " tail", " ears and tail",
        ],
        contains=["kemonomimi", "bunny", "pokemon", "furry",
                  "pony", "animal", "dinosaur", "antler",
                  "hoof", "paw", "beast", "dragon",
                  "demon", "angel", "fox", "wolf",
                  "cat", "dog", "bird", "spider",
                  "snake", "rabbit", "horse", "monster",
                  "alien", "robot", "undead", "vampire",
                  "zombie", "ghost", "fairy", "elf",
                  "dwarf", "orc", "goblin", "slime",
                  "digimon", "anthro", "insect",
                  "boar", "borzoi", "bee", "bees",
                  "bull", "camel", "deer",
                  "fish", "frog", "goat", "hawk",
                  "lizard", "monkey", "owl", "pig",
                  "rat", "scorpion", "shark", "tiger",
                  "turtle", "whale",
                  "arachne", "anubis", "abyssal",
                  "android", "apostle", "basilisk",
                  "behemoth", "cerberus", "chimera",
                  "cyclops", "dryad", "golem",
                  "harpy", "hippogriff", "hydra",
                  "imp", "kraken", "lamia",
                  "leviathan", "mammoth", "minotaur",
                  "naga", "nymph", "pegasus",
                  "phoenix", "salamander", "satyr",
                  "siren", "sphinx", "succubus",
                  "tengu", "unicorn", "wyvern",
                  "youkai", "yokai",
                  ],
        regex_patterns=[
            r"(cat|dog|fox|rabbit|wolf|bunny|cow|tiger|lion|bear|horse|dragon|snake|fox|shark|dolphin|whale)(girl|boy|ears|tail)$",
        ],
    ),
    # 8. setting & background
    SuperCategory(
        name="setting & background",
        description="Indoor/outdoor locations, atmosphere, architecture, weather",
        exact={
            "alley", "balcony", "beach", "bridge", "building", "canyon",
            "castle", "cave", "cemetery", "church", "city", "classroom",
            "cliff", "cloud", "clouds", "corridor", "couch", "desert",
            "field", "forest", "garden", "grass", "hallway", "hospital",
            "hotel", "house", "island", "jungle", "lake", "library",
            "meadow", "mountain", "mountains", "ocean", "office", "park",
            "path", "pathway", "pier", "pool", "rain", "river", "road",
            "roof", "room", "ruins", "sea", "sky", "snow", "space",
            "spring", "stairs", "stairway", "store", "street", "subway",
            "swamp", "temple", "tower", "train station", "tunnel",
            "university", "valley", "village", "volcano", "waterfall",
            "water", "window", "yard", "zoo",
            "day", "night", "sunset", "sunrise", "dawn", "dusk",
            "noon", "midnight", "evening", "morning",
            "indoors", "outdoors", "outside", "inside",
            "blue sky", "cloudy sky", "starry sky", "night sky",
            "rainbow", "fog", "mist", "storm", "thunder", "lightning",
            "wind", "breeze", "snowing", "raining",
            "fantasy landscape", "sci-fi landscape", "urban", "rural",
            "flower field", "wheat field", "battlefield",
            "lava", "fire", "smoke",
        },
        prefixes=[
            "indoor ", "outdoor ", "night ", "day ", "sunset ",
            "sunrise ", "winter ", "summer ", "spring ", "autumn ",
            "forest ", "mountain ", "ocean ", "city ",
        ],
        suffixes=[
            " landscape", " background", " scenery", " scene",
            " view", " room", " field",
        ],
        contains=["background", "landscape", "scenery",
                  "floor", "wall", "tree", "bed", "chair",
                  "interior", "door",
                  "sky", "cloud", "water", "ocean", "sea",
                  "river", "lake", "forest", "mountain", "hill",
                  "rain", "snow", "sun", "sunset", "sunrise",
                  "night", "city", "town", "village", "castle",
                  "church", "temple", "shrine", "school", "park",
                  "garden", "bridge", "road", "street", "room",
                  "window", "stairs", "gate", "cave", "island",
                  "beach", "cliff", "valley", "field", "meadow",
                  "architecture", "aquarium", "bath",
                  ],
        regex_patterns=[
            r"^(indoors?|outdoors?|outside|inside)$",
            r"(sky|cloud|rain|snow|fog|mist|wind|storm|sunset|sunrise|night|morning|evening|dawn|dusk)$",
        ],
    ),
    # 9. franchise & fandom
    SuperCategory(
        name="franchise & fandom",
        description="Anime/manga/game series, named characters, fandoms",
        exact={
            # Will mostly be matched by regex patterns for named characters
            # and series names. This set catches common franchise terms.
            "cosplay", "fan art", "parody", "crossover",
        },
        prefixes=[],
        suffixes=[
            " (series)", " (character)", " (cosplay)",
            " (game)", " (anime)", " (manga)",
        ],
        contains=[
            "series", "fandom", "franchise", "cosplay",
            "fate", "genshin", "touhou", "arknights",
            "azur lane", "fgo", "granblue",
            "kantai", "love live", "idol",
            "vocaloid", "hololive", "nijisanji",
            "pokemon", "digimon", "brawl",
            "star rail", "honkai", "zepeto",
            "wow", "warcraft", "dota",
            "overwatch", "valorant", "league",
        ],
        regex_patterns=[
            # Named character pattern: "firstname lastname" with capital letters
            # won't work since concepts are lowercase — skip
        ],
    ),
    # 10. art style & technique
    SuperCategory(
        name="art style & technique",
        description="Art styles, techniques, media, camera framing, rendering",
        exact={
            "3d", "anime", "artwork", "cartoon", "cel shading",
            "chibi", "comic", "comics", "detailed", "digital art",
            "flat color", "greyscale", "high contrast", "illustration",
            "lineart", "monochrome", "oil painting", "pixel art",
            "realistic", "sketch", "watercolor", "watercolour",
            "traditional art", "vector", "voxel",
            "close-up", "cowboy shot", "full body", "portrait",
            "profile", "upper body", "wide shot", "from above",
            "from below", "from behind", "from side", "dutch angle",
            "birds eye view", "worms eye view", "panorama",
            "depth of field", "motion blur", "lens flare",
            "bokeh", "tilt shift", "vignette",
            "cinematic lighting", "dramatic lighting",
            "photorealistic", "photorealism", "surreal", "abstract",
            "impressionist", "expressionist", "art deco", "art nouveau",
            "baroque", "gothic", "minimalist", "pop art", "steampunk",
            "cyberpunk", "retro", "vintage", "vaporwave",
            "pastel", "neon", "noir", "silhouette",
        },
        prefixes=[
            "art ", "style ", "technique ", "camera ", "render ",
        ],
        suffixes=[
            " style", " art", " painting", " drawing", " sketch",
            " rendering", " render", " shot", " angle", " view",
            " lighting", " effect", " filter",
        ],
        contains=[
            "style", "art style", "render", "lighting", "angle",
            "shot", "view", "stripe", "pattern", "color",
            "design", "anime",
            "blur", "animation", "animated",
            "medium", "amime", "aniime", "animie",
            "biopunk", "solarpunk", "atompunk",
            "argyle", "plaid", "checkered", "polka",
        ],
        regex_patterns=[
            r"^(close-up|cowboy shot|full body|portrait|profile|upper body|wide shot)",
            r"^(from\s+(above|below|behind|side))",
            r"(realistic|surreal|abstract|impressionist|minimalist)",
        ],
    ),
    # 11. seasons & events
    SuperCategory(
        name="seasons & events",
        description="Seasons, holidays, events, time periods, festivals",
        exact={
            "spring", "summer", "autumn", "fall", "winter",
            "christmas", "halloween", "new year", "valentine",
            "valentines day", "easter", "thanksgiving",
            "birthday", "anniversary", "wedding", "festival",
            "carnival", "party", "celebration", "ceremony",
            "graduation", "fireworks", "firework",
            "cherry blossom", "cherry blossoms", "hanami",
            "christmas tree", "pumpkin", "jack-o-lantern",
            "snowman", "santa", "sleigh", "wreath",
            "beach episode", "hot spring", "onsen",
            "school festival", "culture festival",
            "tanabata", "setsubun", "obon",
        },
        prefixes=["season ", "holiday ", "event "],
        suffixes=[
            " festival", " day", " season", " event",
            " celebration", " holiday",
        ],
        contains=[
            "christmas", "halloween", "valentine", "easter",
            "festival", "celebration", "fireworks",
        ],
        regex_patterns=[
            r"^(spring|summer|autumn|fall|winter)\b",
        ],
    ),
    # 12. text & language
    SuperCategory(
        name="text & language",
        description="Language text, text types, numbers, logos, watermarks, speech",
        exact={
            "text", "logo", "watermark", "signature", "stamp",
            "english text", "japanese text", "chinese text", "korean text",
            "cyrillic text", "french text", "german text", "spanish text",
            "italian text", "portuguese text", "thai text", "vietnamese text",
            "translated", "translation", "subtitle", "subtitles",
            "caption", "speech bubble", "thought bubble",
            "speech", "dialogue", "quote", "title", "heading",
            "credits", "copyright", "disclaimer", "notice",
            "barcode", "qr code", "number", "letter", "character",
            "kanji", "hiragana", "katakana", "romaji",
            "comic lettering", "sound effects", "sfx",
            "artist name", "username", "url", "website",
            "heart", "star", "musical note", "sparkle",
            "sparkles", "dust", "particle", "particles",
        },
        prefixes=["text ", "logo ", "written "],
        suffixes=[
            " text", " logo", " watermark", " signature",
            " bubble", " caption",
        ],
        contains=[
            "text", "logo", "watermark", "signature",
            "bubble", "subtitle", "caption", "username",
            "dialogue", "speech", "font", "writing",
        ],
        regex_patterns=[
            r"(english|japanese|chinese|korean|cyrillic|french|german|spanish|italian|portuguese|thai|vietnamese)\s+text",
        ],
    ),
    # 13. lighting & effects
    SuperCategory(
        name="lighting & effects",
        description="Lighting types, visual effects, color grading, particles",
        exact={
            "backlighting", "bloom", "bright", "dark", "darkness",
            "dramatic lighting", "glow", "glowing", "gradient",
            "haze", "highlights", "lens flare", "light", "light rays",
            "lighting", "low key", "natural light", "neon glow",
            "rim lighting", "shadow", "shadows", "silhouette",
            "soft lighting", "spotlight", "sunlight", "sunbeams",
            "underlighting", "ambient light", "candlelight",
            "flashlight", "moonlight", "starlight", "streetlight",
            "firelight", "twilight", "backlight",
            "colorful", "gradient background", "plasma", "electric",
            "energy", "energy blast", "magic", "magical",
            "aura", "force field", "shield", "barrier",
            "explosion", "smoke", "steam", "spark", "sparks",
            "flame", "flames", "fire", "ice", "crystal",
            "reflection", "reflections", "transparent",
            "translucent", "opacity",
        },
        prefixes=[
            "light ", "dark ", "glowing ", "bright ", "shadow ",
        ],
        suffixes=[
            " light", " lighting", " glow", " glow", " flare",
            " shadow", " shine", " effect", " ray", " beam",
            " reflection",
        ],
        contains=[
            "lighting", "glow", "shadow", "flare",
            "gradient", "bloom",
        ],
        regex_patterns=[
            r"(backlight|rim\s+light|spot|sun|moon|star|candle|flash|street|fire)ing$",
        ],
    ),
    # 14. composition & format
    SuperCategory(
        name="composition & format",
        description="Composition rules, image formats, quality tags, aspect ratios",
        exact={
            "absurdres", "highres", "incredibly absurdres", "lowres",
            "masterpiece", "best quality", "high quality", "low quality",
            "medium quality", "worst quality", "normal quality",
            "photorealistic", "ultra detailed", "detailed",
            "wallpaper", "desktop", "mobile", "phone wallpaper",
            "panorama", "panoramic", "widescreen", "fullscreen",
            "4k", "8k", "hd", "uhd", "sd",
            "aspect ratio", "16:9", "4:3", "21:9", "1:1",
            "crop", "cropped", "border", "frame", "frames",
            "simple background", "white background", "black background",
            "grey background", "transparent background",
            "gradient background", "pattern background",
            "solo", "multiple", "group", "pair",
            "collage", "split screen", "diptych", "triptych",
            "comic panel", "manga panel", "4koma", "strip",
            "cover", "thumbnail", "icon", "avatar",
            "sketch page", "reference sheet", "character sheet",
            "concept art", "design sheet", "turnaround",
        },
        prefixes=[
            "quality ", "resolution ", "format ", "composition ",
        ],
        suffixes=[
            " quality", " resolution", " res", " background",
            " wallpaper", " panel", " sheet", " page",
        ],
        contains=[
            "quality", "resolution", "absurdres", "highres", "lowres",
            "masterpiece", "background", "focus", "inset",
            "connection", "panel", "comic", "manga",
        ],
        regex_patterns=[
            r"^(absurdres|highres|lowres|incredibly\s+absurdres)$",
            r"(quality|resolution|res)$",
            r"background$",
        ],
    ),
    # 15. relationships & demographics
    SuperCategory(
        name="relationships & demographics",
        description="Age, gender, relationship types, group dynamics, pairings",
        exact={
            "male", "female", "boy", "girl", "man", "woman",
            "men", "women", "boys", "girls",
            "couple", "duo", "trio", "group",
            "friends", "friendship", "partners", "rivals",
            "family", "parent", "child", "sibling", "siblings",
            "mother", "father", "brother", "sister", "son", "daughter",
            "grandmother", "grandfather", "grandparent",
            "uncle", "aunt", "cousin", "nephew", "niece",
            "wife", "husband", "spouse",
            "lover", "boyfriend", "girlfriend", "fiance", "fiancee",
            "crush", "dating", "romance", "married",
            "teacher", "student", "classmate", "coworker", "colleague",
            "boss", "employee", "subordinate", "superior",
            "mentor", "apprentice", "master", "servant",
            "king", "queen", "prince", "princess", "knight",
            "warrior", "soldier", "general", "commander",
            "leader", "follower", "ally", "enemy", "nemesis",
            "hero", "villain", "antihero",
            "teenager", "adult", "elder", "elderly", "child",
            "children", "baby", "infant", "toddler",
            "young", "old", "middle-aged",
            "loli", "shota", "oneesan", "onii-san",
            "imouto", "otouto",
        },
        prefixes=[],
        suffixes=[
            " boy", " girl", " boys", " girls",
            " male", " female", " men", " women",
        ],
        contains=[
            "couple", "group", "family", "friend",
            "difference", "age gap",
            "albino", "androgynous", "ambiguous",
            "progression",
        ],
        regex_patterns=[
            r"^\d+(boys?|girls?|men|women|males?|females?|others?)$",
            r"^(male|female|boy|girl|man|woman|men|women)$",
        ],
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify orphan root concepts into super-category parents",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--create-parents",
        action="store_true",
        help="Create super-category parent concepts in DB (use with --apply)",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Only classify concepts matching this category name (for testing)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of concepts to classify (0 = no limit)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every classified concept name",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Slug helpers (mirrors taxonomy_service.py)
# ---------------------------------------------------------------------------
def slugify_concept_name(value: str) -> str:
    normalized = (value or "").strip().replace("_", " ").lower()
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "concept"


def ensure_unique_slug(db, base_slug: str) -> str:
    slug = base_slug
    idx = 2
    while db.query(Concept.id).filter(Concept.slug == slug).first() is not None:
        slug = f"{base_slug}-{idx}"
        idx += 1
    return slug


# ---------------------------------------------------------------------------
# Phase 1: Create super-category parent concepts
# ---------------------------------------------------------------------------
def create_parent_concepts(db) -> dict[str, int]:
    """Create super-category concepts and return {name: concept_id}."""
    name_to_id: dict[str, int] = {}

    for cat in CATEGORIES:
        # Check if concept already exists
        existing = db.query(Concept).filter(
            Concept.canonical_name == cat.name,
        ).first()
        if existing:
            cat.concept_id = existing.id
            name_to_id[cat.name] = existing.id
            print(f"  ✓ Existing: {cat.name} (id={existing.id})")
            continue

        slug = ensure_unique_slug(db, slugify_concept_name(cat.name))
        concept = Concept(
            canonical_name=cat.name,
            slug=slug,
            description=cat.description,
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(concept)
        db.flush()
        cat.concept_id = concept.id
        name_to_id[cat.name] = concept.id
        print(f"  + Created: {cat.name} (id={concept.id})")

    return name_to_id


# ---------------------------------------------------------------------------
# Phase 2: Classify orphans
# ---------------------------------------------------------------------------
def classify_concept(name: str) -> str | None:
    """Return the matching super-category name for a concept, or None."""
    name_lower = name.lower().strip()

    for cat in CATEGORIES:
        # 1. Exact match
        if name_lower in cat.exact:
            return cat.name

        # 2. Prefix patterns
        for prefix in cat.prefixes:
            if name_lower.startswith(prefix):
                return cat.name

        # 3. Suffix patterns
        for suffix in cat.suffixes:
            if name_lower.endswith(suffix):
                return cat.name

        # 4. Contains patterns
        for substr in cat.contains:
            if substr in name_lower:
                return cat.name

        # 5. Regex patterns
        for pattern in cat.regex_patterns:
            if re.search(pattern, name_lower):
                return cat.name

    return None


def get_orphan_concepts(db) -> list[tuple[int, str]]:
    """Return (id, canonical_name) for all orphan root concepts
    that are NOT existing tree roots and have no children."""
    # Get IDs of concepts that ARE parents (have children)
    parent_ids_sq = (
        db.query(Concept.parent_concept_id)
        .filter(Concept.parent_concept_id.isnot(None))
        .distinct()
        .subquery()
    )

    rows = (
        db.query(Concept.id, Concept.canonical_name)
        .filter(Concept.parent_concept_id.is_(None))
        .filter(Concept.id.notin_(EXISTING_TREE_ROOT_IDS))
        .filter(~Concept.id.in_(parent_ids_sq))  # not a parent of anyone
        .filter(~Concept.slug.startswith("super-"))  # never classify super-category parents
        .order_by(Concept.canonical_name.asc())
        .all()
    )
    return [(r[0], r[1]) for r in rows]


def _phase1_create_parents(db, args: argparse.Namespace) -> None:
    """Phase 1: Create super-category parent concepts."""
    print("=" * 60)
    print("Phase 1: Creating super-category parent concepts")
    print("=" * 60)
    if not args.apply:
        print("DRY-RUN: Would create the following parent concepts:")
        for cat in CATEGORIES:
            print(f"  - {cat.name}: {cat.description}")
    else:
        name_to_id = create_parent_concepts(db)
        db.commit()
        print(f"\nCreated/verified {len(name_to_id)} parent concepts.")


def _resolve_parent_ids(db) -> None:
    """Populate cat.concept_id for all categories that exist in DB."""
    for cat in CATEGORIES:
        existing = db.query(Concept).filter(
            Concept.canonical_name == cat.name,
        ).first()
        if existing:
            cat.concept_id = existing.id


def _resolve_target_categories(args: argparse.Namespace) -> list[SuperCategory]:
    """Return the categories to classify against (filtered if --category given)."""
    if not args.category:
        return list(CATEGORIES)
    target_names = {c.strip().lower() for c in args.category.split(",")}
    return [c for c in CATEGORIES if c.name in target_names]


def _run_classification(
    orphans: list[tuple[int, str]],
    target_categories: list[SuperCategory],
    args: argparse.Namespace,
) -> tuple[dict[str, list[tuple[int, str]]], list[tuple[int, str]]]:
    """Classify all orphan concepts. Returns (classifications, unclassified)."""
    classifications: dict[str, list[tuple[int, str]]] = {
        cat.name: [] for cat in target_categories
    }
    unclassified: list[tuple[int, str]] = []
    target_names = {c.name for c in target_categories}

    for concept_id, name in orphans:
        matched = classify_concept(name)
        if args.category and (matched is None or matched not in target_names):
            continue
        if matched and matched in classifications:
            classifications[matched].append((concept_id, name))
        else:
            unclassified.append((concept_id, name))

    return classifications, unclassified


def _print_stats(
    classifications: dict[str, list[tuple[int, str]]],
    unclassified: list[tuple[int, str]],
    target_categories: list[SuperCategory],
    verbose: bool,
) -> int:
    """Print classification stats. Returns total_classified count."""
    print("Classification Results:")
    print("-" * 40)
    total_classified = 0
    for cat in target_categories:
        count = len(classifications[cat.name])
        total_classified += count
        marker = (
            f"→ parent id={cat.concept_id}"
            if cat.concept_id
            else "(parent not created yet)"
        )
        print(f"  {cat.name}: {count} concepts {marker}")

        if verbose and classifications[cat.name]:
            for cid, cname in classifications[cat.name][:20]:
                print(f"    - #{cid} {cname}")
            if len(classifications[cat.name]) > 20:
                print(f"    ... and {len(classifications[cat.name]) - 20} more")

    print(f"\n  Total classified: {total_classified}")
    print(f"  Unclassified: {len(unclassified)}")

    if verbose and unclassified:
        print("\n  Unclassified concepts (first 50):")
        for cid, cname in unclassified[:50]:
            print(f"    - #{cid} {cname}")
        if len(unclassified) > 50:
            print(f"    ... and {len(unclassified) - 50} more")

    return total_classified


def _apply_assignments(
    db,
    classifications: dict[str, list[tuple[int, str]]],
    target_categories: list[SuperCategory],
) -> int:
    """Apply parent_concept_id assignments to DB. Returns count applied."""
    print("\nApplying parent assignments...")
    applied = 0
    for cat in target_categories:
        if cat.concept_id is None:
            print(f"  ⚠ Skipping {cat.name} — parent concept not created yet")
            continue
        for concept_id, _ in classifications[cat.name]:
            concept = db.query(Concept).filter(Concept.id == concept_id).first()
            if concept and concept.parent_concept_id is None:
                concept.parent_concept_id = cat.concept_id
                concept.updated_at = datetime.utcnow()
                applied += 1
    db.commit()
    print(f"Applied {applied} parent assignments.")
    return applied


def main() -> int:
    args = parse_args()

    db = SessionLocal()
    try:
        # ── Phase 1: Create parents ──────────────────────────────────────
        if args.create_parents:
            _phase1_create_parents(db, args)

        # ── Phase 2: Classify orphans ────────────────────────────────────
        print("\n" + "=" * 60)
        print("Phase 2: Classifying orphan concepts")
        print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
        print("=" * 60)

        orphans = get_orphan_concepts(db)
        if args.limit and args.limit > 0:
            orphans = orphans[: args.limit]

        print(f"Orphan concepts to classify: {len(orphans)}\n")

        _resolve_parent_ids(db)
        target_categories = _resolve_target_categories(args)
        classifications, unclassified = _run_classification(
            orphans, target_categories, args,
        )
        total_classified = _print_stats(
            classifications, unclassified, target_categories, args.verbose,
        )

        # ── Phase 3: Apply ───────────────────────────────────────────────
        if args.apply and total_classified > 0:
            _apply_assignments(db, classifications, target_categories)
        elif not args.apply:
            print("\nNo changes applied. Re-run with --apply to execute.")

        return 0

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

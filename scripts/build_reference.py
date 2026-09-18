"""Génère le référentiel du produit fusionné depuis les fichiers d'`agriflow`.

Généré et non retranscrit : recopier à la main un tableau de Kc est exactement
l'endroit où une valeur fausse mais plausible entre, et aucun test en aval ne la
rattrape. Ce script ne modifie **aucune valeur numérique** ; il renomme les
codes, réunit les attributs logistiques d'`atlasagri`, et éclate les stades en
lignes.
"""
import json, pathlib, sys

SRC = pathlib.Path("/home/user/agriflow-/data/seed")
OUT = pathlib.Path("/home/user/agriSaas-/backend/app/data")

CODE = {"olive":"OLIVE","citrus":"CITRUS","tomato":"TOMATO","potato":"POTATO",
        "wheat":"WHEAT","barley":"BARLEY","pepper":"PEPPER","strawberry":"STRAWBERRY",
        "melon":"MELON","grape":"GRAPE"}
CATEGORY = {"arboriculture":"ARBORICULTURE","maraichage":"MARKET_GARDEN","cereales":"CEREAL"}
# Arboriculture fruitière et vigne de table sont commercialisées comme des
# fruits frais : la catégorie agronomique ne suffit pas à trancher.
CATEGORY_OVERRIDE = {"STRAWBERRY":"SOFT_FRUIT","GRAPE":"VINE"}

# Chaîne du froid : attribut **commercial**, pas une constante FAO.
# Repris d'`atlasagri` là où il existe ; ailleurs, déduit d'une règle explicite.
COLD_CHAIN_FROM_ATLASAGRI = {"TOMATO":True,"CITRUS":True,"OLIVE":False,"WHEAT":False,
                             "STRAWBERRY":True,"POTATO":False}
COLD_CHAIN_RULE = {"PEPPER":True,"MELON":True,"GRAPE":True,"BARLEY":False}

STAGE = [("INITIAL","initial",1,"kc_initial"),
         ("DEVELOPMENT","development",2,None),
         ("MID_SEASON","mid_season",3,"kc_mid"),
         ("LATE_SEASON","late_season",4,"kc_end")]

SOIL_CODE = {"sand":"SAND","loamy_sand":"LOAMY_SAND","sandy_loam":"SANDY_LOAM",
             "loam":"LOAM","silt_loam":"SILT_LOAM","clay_loam":"CLAY_LOAM","clay":"CLAY"}
SYS_CODE = {"drip":"DRIP","micro_sprinkler":"MICRO_SPRINKLER","sprinkler":"SPRINKLER",
            "pivot":"PIVOT","flood":"FLOOD"}

def _cold_chain(code: str) -> bool:
    """Chaîne du froid, sans défaut implicite.

    Une valeur par défaut ferait qu'une culture ajoutée plus tard hériterait
    silencieusement d'un choix que personne n'a fait — et ce choix décide si un
    entrepôt sans froid est écarté comme infaisable. Une culture non couverte
    fait échouer la génération.
    """
    if code in COLD_CHAIN_FROM_ATLASAGRI:
        return COLD_CHAIN_FROM_ATLASAGRI[code]
    if code in COLD_CHAIN_RULE:
        return COLD_CHAIN_RULE[code]
    raise KeyError(
        f"{code} n'est couvert par aucune des deux sources de chaîne du froid. "
        "Ajouter une entrée explicite plutôt qu'un défaut."
    )


def build_crops():
    src = json.load(open(SRC/"crops.json"))
    meta, rows = src["_meta"], src["crops"]
    crops, stages = [], []
    for r in rows:
        code = CODE[r["code"]]
        cat = CATEGORY_OVERRIDE.get(code, CATEGORY[r["category"]])
        ky = r["yield_response_factor_ky"]
        crops.append({
            "code": code, "name_fr": r["name_fr"], "name_en": r["name_en"],
            "category": cat, "is_perennial": r["perennial"],
            "kc_initial": r["kc_initial"], "kc_mid": r["kc_mid"], "kc_end": r["kc_end"],
            "kc_source": r["kc_basis"],
            "stage_lengths_source": r["stage_lengths_source"],
            "cycle_start_month": r.get("cycle_start_month"),
            "root_depth_min_m": r["root_depth_min_m"],
            "root_depth_max_m": r["root_depth_max_m"],
            "depletion_fraction_p": r["depletion_fraction_p"],
            "root_and_depletion_source": (
                "FAO-56 Table 22 — profondeur d'enracinement maximale et fraction "
                "d'épuisement p sans stress, conditions standard."),
            "yield_response_factor_ky": ky,
            "ky_source": (r.get("yield_response_note_fr") if ky is not None else None),
            "ky_absent_reason_fr": (None if ky is not None else r.get(
                "yield_response_note_fr",
                "Aucun coefficient Ky documenté par la FAO-33 pour cette culture : "
                "l'impact sur le rendement n'est pas estimé.")),
            "requires_cold_chain": _cold_chain(code),
            "notes_fr": r.get("notes_fr"),
        })
        lengths = r["stage_lengths_days"]
        # Kc du stade : constant sur INITIAL et MID_SEASON, valeur de fin sur
        # LATE_SEASON. DEVELOPMENT est interpolé par le moteur (FAO-56 fig. 21) ;
        # la ligne porte le Kc de départ pour que la table reste lisible seule.
        for name, key, seq, kc_field in STAGE:
            kc = r[kc_field] if kc_field else r["kc_initial"]
            stages.append({
                "crop_code": code, "stage": name, "sequence": seq,
                "length_days": lengths[key], "kc": kc,
                "source": (
                    r["stage_lengths_source"] + " — Kc : " + r["kc_basis"]
                    if kc_field else
                    r["stage_lengths_source"] + " — Kc interpolé linéairement entre "
                    "Kc initial et Kc mi-saison (FAO-56 figure 21) ; la valeur portée "
                    "ici est le point de départ de l'interpolation."),
            })
    return {"_meta": {
        "generated_from": "agriflow- data/seed/crops.json (branche claude/agriflow-backend-startup-fobs8u)",
        "generation_note": (
            "Généré, jamais retranscrit : aucune valeur numérique n'est modifiée par "
            "la transformation. Les codes passent en anglais majuscule, les attributs "
            "logistiques d'atlasagri sont réunis, les stades sont éclatés en lignes."),
        "primary_source": meta["primary_source"],
        "yield_source": meta["yield_source"],
        "cold_chain_note": (
            "requires_cold_chain est un attribut COMMERCIAL, pas une constante FAO. "
            "Repris d'atlasagri pour les cultures qu'il couvre ; ailleurs déduit d'une "
            "règle explicite (fruits frais et maraîchage : oui ; céréales et olive à "
            "trituration : non). Surchargeable par organisation."),
        "warning": meta["warning"],
    }, "crops": crops, "stages": stages}

def build_soils():
    src = json.load(open(SRC/"soils.json")); meta = src["_meta"]
    out=[]
    for r in src["soils"]:
        out.append({
            "code": SOIL_CODE[r["code"]], "name_fr": r["name_fr"], "name_en": r["name_en"],
            "theta_fc_m3_m3": r["field_capacity"], "theta_wp_m3_m3": r["wilting_point"],
            "theta_fc_range_low": r["field_capacity_range"][0],
            "theta_fc_range_high": r["field_capacity_range"][1],
            "theta_wp_range_low": r["wilting_point_range"][0],
            "theta_wp_range_high": r["wilting_point_range"][1],
            "hydraulic_source": meta["primary_source"],
            "infiltration_rate_mm_per_hour": r["infiltration_rate_mm_per_hour"],
            "infiltration_source": meta["infiltration_source"],
            "description_fr": r.get("description_fr"),
        })
    return {"_meta": {"generated_from":"agriflow- data/seed/soils.json",
                      "primary_source": meta["primary_source"],
                      "infiltration_source": meta["infiltration_source"],
                      "warning": meta["warning"]}, "soils": out}

def build_systems():
    src = json.load(open(SRC/"irrigation_systems.json")); meta = src["_meta"]
    out=[]
    for r in src["systems"]:
        out.append({
            "code": SYS_CODE[r["code"]], "name_fr": r["name_fr"], "name_en": r["name_en"],
            "efficiency": r["efficiency"],
            "efficiency_range_low": r["efficiency_range"][0],
            "efficiency_range_high": r["efficiency_range"][1],
            "efficiency_source": meta["source"],
            "wets_whole_surface": r["wets_whole_surface"],
            "description_fr": r.get("description_fr"),
        })
    return {"_meta": {"generated_from":"agriflow- data/seed/irrigation_systems.json",
                      "source": meta["source"], "warning": meta["warning"]}, "systems": out}

OUT.mkdir(parents=True, exist_ok=True)
for name, payload in (("crops.json", build_crops()),
                      ("soils.json", build_soils()),
                      ("irrigation_systems.json", build_systems())):
    (OUT/name).write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("wrote", name)

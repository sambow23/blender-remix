import os
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))

print(f"RTX Remix Importer: Addon directory set to: {ADDON_DIR}")

# Material conversion constants
# Aperture PBR material types in RTX Remix
MATERIAL_TYPES = {
    "AperturePBR": "STANDARD",
    "AperturePBR_Opacity": "OPACITY",
    "AperturePBR_Translucent": "TRANSLUCENT",
    "AperturePBR_SpriteSheet": "SPRITESHEET",
    "AperturePBR_Model": "MODEL",
    "AperturePBR_Normal": "NORMAL",
    "AperturePBR_Portal": "PORTAL",
}

# RTX Remix texture suffixes to Blender shader inputs mapping
TEXTURE_SUFFIX_MAP = {
    "_BaseColor.a.rtex.dds": "base_color",
    "_BaseColor": "base_color",
    "_Metallic.m.rtex.dds": "metallic",
    "_Metallic": "metallic",
    "_Roughness.r.rtex.dds": "roughness",
    "_Roughness": "roughness",
    "_OTH_Normal.n.rtex.dds": "normal",
    "_Normal_2_OTH_Normal.n.rtex.dds": "normal",
    "_Normal.n.rtex.dds": "normal",
    "_Normal": "normal",
    "_Emissive.e.rtex.dds": "emission",
    "_Emissive": "emission",
    "_Emissive_orange.e.rtex.dds": "emission",
} 
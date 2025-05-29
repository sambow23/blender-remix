import bpy
import os
from .constants import TEXTURE_SUFFIX_MAP

def find_texture_path(texture_ref, texture_dir):
    """
    Find the actual texture path from a USD reference and base texture directory

    Args:
        texture_ref: USD asset path reference
        texture_dir: Base texture directory path

    Returns:
        str: Full path to the texture file
    """
    # If it's not a valid reference, return None
    if not texture_ref or texture_ref == "@@":
        return None

    # Extract the texture file name from the asset reference
    texture_path = str(texture_ref)
    # Remove leading @ if present (common in USD asset references)
    if texture_path.startswith('@'):
        texture_path = texture_path[1:]
    if texture_path.endswith('@'):
        texture_path = texture_path[:-1]

    # Handle RTX Remix relative paths (../assets/...)
    if texture_path.startswith("../assets/"):
        # Check if we have access to the USD file path
        # Note: Accessing active_operator might be brittle here.
        # Consider passing the usd_file_path explicitly if possible.
        if hasattr(bpy, "context") and hasattr(bpy.context, "active_operator") and hasattr(bpy.context.active_operator, "filepath"):
            usd_file_path = bpy.context.active_operator.filepath

            # Navigate up to the mod root directory
            mod_dir = os.path.dirname(os.path.dirname(usd_file_path))

            # Remove the "../" prefix to get the path relative to mod directory
            rel_path = texture_path[3:]  # Remove "../"
            resolved_path = os.path.join(mod_dir, rel_path)

            if os.path.exists(resolved_path):
                print(f"Resolved texture path: {resolved_path}")
                return resolved_path

    # If path starts with . or / or contains :, it's likely already a complete path
    if not texture_path.startswith('.') and not texture_path.startswith('/') and not ':' in texture_path:
        # It's a relative path from the texture directory
        if texture_dir:
            texture_path = os.path.join(texture_dir, texture_path)

    # If the file doesn't exist, check for other file extensions
    if not os.path.exists(texture_path):
        # Try dds if not specified
        base_path, ext = os.path.splitext(texture_path)
        if ext.lower() != '.dds':
            dds_path = base_path + '.dds'
            if os.path.exists(dds_path):
                return dds_path

        # Try JPEG and PNG fallbacks
        for ext in ['.jpg', '.jpeg', '.png']:
            alt_path = base_path + ext
            if os.path.exists(alt_path):
                return alt_path

        # If we still haven't found it, let's try to find the file by name in the texture_dir
        if texture_dir and os.path.exists(texture_dir):
            texture_name = os.path.basename(texture_path)
            for root, _, files in os.walk(texture_dir):
                for file in files:
                    if file.lower() == texture_name.lower():
                        return os.path.join(root, file)

        # Try to find by RTX Remix texture suffix patterns
        texture_name = os.path.basename(texture_path)
        # Extract the base name without suffixes like _BaseColor.a.rtex.dds
        base_name = texture_name
        for suffix in ['_BaseColor', '_Metallic', '_Roughness', '_Normal', '_OTH_Normal', '_Emissive']:
            if suffix in base_name:
                base_name = base_name.split(suffix)[0]
                break

        # If we found a base name, try to locate related textures
        if base_name and base_name != texture_name and texture_dir and os.path.exists(texture_dir):
            for root, _, files in os.walk(texture_dir):
                for file in files:
                    if file.startswith(base_name):
                        # Check if it matches the type we're looking for
                        for suffix, input_type in TEXTURE_SUFFIX_MAP.items():
                            if suffix in file:
                                print(f"Found related texture by pattern matching: {os.path.join(root, file)}")
                                return os.path.join(root, file)

    return texture_path

def load_texture(file_path, is_normal=False, is_non_color=False):
    """
    Load a texture from a file path.

    Args:
        file_path: Path to the texture file
        is_normal: Whether this is a normal map
        is_non_color: Whether this is a non-color data texture

    Returns:
        bpy.types.Image: Loaded image or None
    """
    try:
        from .texture_loader import load_texture_smart
        return load_texture_smart(file_path, is_normal, is_non_color)
    except ImportError:
        print(f"  Attempting to load texture (fallback): '{file_path}'")
        if not file_path or not os.path.exists(file_path):
            print(f"    WARNING: Texture file not found or path invalid: {file_path}")
            return None

        # Check if image is already loaded
        image_name = os.path.basename(file_path)
        if image_name in bpy.data.images:
            print(f"    Texture already loaded in Blender: {image_name}")
            image = bpy.data.images[image_name]
        else:
            print(f"    Loading texture image file: {file_path}")
            try:
                # Load the image
                image = bpy.data.images.load(file_path, check_existing=True)
                image.name = image_name
                print(f"    Successfully loaded image '{image.name}'")

                # Set texture settings
                if is_normal or is_non_color:
                    image.colorspace_settings.name = 'Non-Color'
                    print(f"      Set colorspace to Non-Color")
                else:
                    # Ensure sRGB for color textures unless specified otherwise
                    image.colorspace_settings.name = 'sRGB'
                    print(f"      Set colorspace to sRGB")

                # Handle DDS mip maps if supported by Blender
                if file_path.lower().endswith('.dds'):
                    image.use_generated_mipmap = True
                    print(f"      Enabled generated mipmaps for DDS")

                    # For DDS normal maps, ensure they're properly handled
                    if is_normal:
                        image.colorspace_settings.name = 'Non-Color'
                        # Some DDS normal maps need to be flipped in the G channel
                        print("      Note: DDS Normal Map. If incorrect, manually flip green channel in shader nodes.")

            except Exception as e:
                print(f"    ERROR: Could not load texture: {file_path} - {str(e)}")
                import traceback
                traceback.print_exc()
                return None

        return image


def resolve_material_asset_path(file_path, usd_file_path_context=None):
    """
    Resolve an asset path from a USD material, relative to the USD file.

    Args:
        file_path: File path string from USD material (e.g., @../assets/texture.dds@)
        usd_file_path_context: The absolute path of the main imported USD file.

    Returns:
        str: Resolved absolute file path or the original path if not resolved.
    """
    print(f"  Attempting to resolve texture path: '{file_path}'") # LOGGING
    if not file_path:
        return None

    original_file_path = file_path # Keep for warning message

    # Clean up common USD asset path syntax like @@ or asset://
    if file_path.startswith('@'):
        file_path = file_path[1:]
    if file_path.endswith('@'):
        file_path = file_path[:-1]
    if file_path.startswith("asset://"):
        file_path = file_path[8:]

    cleaned_path = file_path # LOGGING
    print(f"    Cleaned path: '{cleaned_path}'") # LOGGING

    # If it's already absolute, check existence and return
    if os.path.isabs(cleaned_path):
        print(f"    Path is absolute: '{cleaned_path}'") # LOGGING
        if os.path.exists(cleaned_path):
            print(f"    SUCCESS: Absolute path exists: '{cleaned_path}'") # LOGGING
            return cleaned_path
        else:
            # Maybe try common extensions if absolute path doesn't exist
            base_path, _ = os.path.splitext(cleaned_path)
            for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga']:
                alt_path = os.path.normpath(base_path + ext)
                if os.path.exists(alt_path):
                    print(f"    SUCCESS: Found absolute path with different extension: {alt_path}") # LOGGING
                    return alt_path
            print(f"    WARNING: Absolute path specified but not found: {original_file_path}") # LOGGING
            return original_file_path # Return original if not found

    # --- Relative Path Resolution ---
    if not usd_file_path_context:
        print(f"    WARNING: Cannot resolve relative path '{original_file_path}' without USD file context.") # LOGGING
        return original_file_path # Cannot resolve further

    print(f"    Resolving relative path based on USD context: {usd_file_path_context}") # LOGGING
    usd_dir = os.path.dirname(usd_file_path_context)
    mod_dir = os.path.dirname(usd_dir) # Often the structure is /ModName/rtx-remix/capture.usda
    mod_root_dir = os.path.dirname(mod_dir) # Go one level higher for cases like /ModName/assets

    potential_base_dirs = [
        usd_dir,                                    # Relative to the USD file itself
        mod_dir,                                    # Relative to the rtx-remix folder
        mod_root_dir,                               # Relative to the mod's root folder
        os.path.join(mod_dir, "assets"),            # Common assets folder within rtx-remix
        os.path.join(mod_root_dir, "assets"),       # Common assets folder in mod root
        os.path.join(mod_dir, "captures", "textures"), # Remix capture textures
        os.path.join(usd_dir, "textures"), # Added: textures dir next to USD
    ]

    # Handle specific Remix patterns like "../assets/"
    if cleaned_path.startswith(("../assets/", "..\\assets\\")):
        rel_path = cleaned_path.split("assets/", 1)[-1] if "assets/" in cleaned_path else cleaned_path.split("assets\\", 1)[-1]
        print(f"    Detected '../assets/' pattern. Relative path part: '{rel_path}'") # LOGGING
        # Primarily check relative to the mod dir (parent of USD dir)
        check_path = os.path.normpath(os.path.join(mod_dir, "assets", rel_path))
        print(f"      Checking: {check_path}") # LOGGING
        if os.path.exists(check_path):
             print(f"    SUCCESS: Resolved '../assets/' path: {check_path}") # LOGGING
             return check_path
        # Fallback check relative to USD dir parent's parent
        check_path_alt = os.path.normpath(os.path.join(mod_root_dir, "assets", rel_path))
        print(f"      Checking (alt): {check_path_alt}") # LOGGING
        if os.path.exists(check_path_alt):
             print(f"    SUCCESS: Resolved '../assets/' path (alt): {check_path_alt}") # LOGGING
             return check_path_alt

    # Handle paths starting like "textures\..." or "materials\..."
    elif cleaned_path.startswith(("textures/", "textures\\")) or cleaned_path.startswith(("materials/", "materials\\")):
        print(f"    Detected relative subdirectory pattern: '{cleaned_path}'") # LOGGING
        # Check relative to USD directory first
        check_path = os.path.normpath(os.path.join(usd_dir, cleaned_path))
        print(f"      Checking: {check_path}") # LOGGING
        if os.path.exists(check_path):
            print(f"    SUCCESS: Resolved subdirectory path relative to USD: {check_path}") # LOGGING
            return check_path
        # Fallback: Check relative to mod directory
        check_path_mod = os.path.normpath(os.path.join(mod_dir, cleaned_path))
        print(f"      Checking: {check_path_mod}") # LOGGING
        if os.path.exists(check_path_mod):
            print(f"    SUCCESS: Resolved subdirectory path relative to mod dir: {check_path_mod}") # LOGGING
            return check_path_mod


    # Check relative paths based on potential base dirs more generically
    print(f"    Checking generic potential base directories...") # LOGGING
    for base_dir in potential_base_dirs:
        if not base_dir or not os.path.isdir(base_dir):
             continue

        potential_path = os.path.normpath(os.path.join(base_dir, cleaned_path))
        print(f"      Checking: {potential_path}") # LOGGING

        if os.path.exists(potential_path):
            print(f"    SUCCESS: Found texture at: {potential_path}") # LOGGING
            return potential_path

        # Try common extensions if exact match failed
        base_name_part, _ = os.path.splitext(os.path.basename(cleaned_path))
        dir_name_part = os.path.dirname(potential_path) # Use the dir from the current attempt

        for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga']:
             test_path = os.path.join(dir_name_part, base_name_part + ext)
             test_path = os.path.normpath(test_path)
             if os.path.exists(test_path):
                 print(f"    SUCCESS: Found texture with different extension at: {test_path}") # LOGGING
                 return test_path


    # Last resort: Search common texture directories recursively by filename
    print(f"    Last resort: Searching recursively in common texture dirs...") # LOGGING
    basename = os.path.basename(cleaned_path)
    search_dirs = [
        os.path.join(mod_dir, "assets"),
        os.path.join(mod_root_dir, "assets"),
        os.path.join(usd_dir, "textures"), # Added
        os.path.join(mod_dir, "captures", "textures"),
        os.path.join(mod_root_dir, "textures") # Generic textures folder
    ]

    for search_dir in search_dirs:
        if search_dir and os.path.exists(search_dir):
            print(f"      Searching recursively in: {search_dir}") # LOGGING
            for root, _, files in os.walk(search_dir):
                for file in files:
                    # Simple basename match first
                    if file.lower() == basename.lower():
                       found_path = os.path.normpath(os.path.join(root, file))
                       print(f"    SUCCESS: Found texture by name search at: {found_path}") # LOGGING
                       return found_path
                    # Try matching ignoring extension
                    if os.path.splitext(file)[0].lower() == os.path.splitext(basename)[0].lower():
                        found_path = os.path.normpath(os.path.join(root, file))
                        print(f"    SUCCESS: Found texture by base name search at: {found_path}") # LOGGING
                        return found_path


    # If not found after all attempts
    print(f"    FAILURE: Could not resolve texture path: {original_file_path} relative to {usd_file_path_context}") # LOGGING
    return original_file_path # Return original path as fallback 
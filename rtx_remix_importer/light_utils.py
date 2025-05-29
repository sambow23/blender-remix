import bpy
import math
import os
try:
    from pxr import Usd, UsdGeom, Sdf, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

def create_light_from_usd(light_prim, stage, scene_scale=1.0):
    """
    Create a Blender light from a USD light prim.

    Args:
        light_prim: Usd.Prim representing the light.
        stage: The Usd.Stage containing the prim.
        scene_scale (float): Global scale factor for the scene.

    Returns:
        bpy.types.Object: Created Blender light object or None on failure.
    """
    if not USD_AVAILABLE:
        print("USD libraries not available, cannot create lights.")
        return None

    try:
        light_name = light_prim.GetName()
        prim_type = light_prim.GetTypeName() # e.g., SphereLight, DiskLight

        # --- Get Transform ---
        xformable = UsdGeom.Xformable(light_prim)
        # Get world transform at default time
        # Note: Blender typically uses Z-up, USD can be Y-up or Z-up.
        # We need to handle potential axis conversion.
        world_transform = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        translate = world_transform.ExtractTranslation()
        # Rotation extraction is complex (quaternion -> euler)
        rotation_quat = world_transform.ExtractRotationQuat()
        # Scale requires decomposing the matrix, often lights aren't scaled directly in transform
        # Scale components affect Area light size properties instead.

        # Handle potential axis conversion (assuming Blender is Z-up)
        up_axis = UsdGeom.GetStageUpAxis(stage)
        blender_location = (translate[0], translate[1], translate[2])
        blender_rotation_quat = (rotation_quat.GetReal(), *rotation_quat.GetImaginary())

        if up_axis == UsdGeom.Tokens.y:
            # Convert Y-up USD to Z-up Blender
            # Swap Y and Z, negate new Y for position
            blender_location = (translate[0], -translate[2], translate[1])
            # Convert quaternion rotation
            # Easiest might be to convert USD quat to Euler, then apply axis swap logic, then back to Blender Euler/Quat
            # For simplicity now, we'll use the direct quat and note it might be wrong for Y-up stages.
            print(f"Warning: Y-up stage detected for light '{light_name}'. Rotation might be incorrect.")
            # A common Y-up to Z-up rotation adjustment is rotating -90 degrees around X-axis *after* other rotations.
            # This needs careful application depending on USD rotation order.

        # Apply scene scale to location
        blender_location = (
            blender_location[0] * scene_scale,
            blender_location[1] * scene_scale,
            blender_location[2] * scene_scale
        )

        # --- Get Light Properties (using .Get() with fallback) ---
        def get_attr_value(prim, attr_name, default_value):
            attr = prim.GetAttribute(f"inputs:{attr_name}")
            return attr.Get(Usd.TimeCode.Default()) if attr and attr.HasValue() else default_value

        intensity = get_attr_value(light_prim, "intensity", 1.0)
        exposure = get_attr_value(light_prim, "exposure", 0.0) # USD exposure is often log2 based
        color = get_attr_value(light_prim, "color", Gf.Vec3f(1.0, 1.0, 1.0))
        radius = get_attr_value(light_prim, "radius", 0.1) # Default small radius
        length = get_attr_value(light_prim, "length", 1.0) # For Cylinder/Rect lights
        width = get_attr_value(light_prim, "width", 1.0) # For Rect lights
        angle = get_attr_value(light_prim, "shaping:cone:angle", 180.0) # For spot lights (if we add them)
        softness = get_attr_value(light_prim, "shaping:cone:softness", 0.0) # For spot lights


        # Calculate combined intensity (Blender uses 'energy')
        # USD Intensity * 2^Exposure = Power (Watts)
        # Blender energy relationship is complex, depends on light type/units.
        # A simple scaling factor might be needed, e.g., * 100, requires testing.
        light_power = intensity * math.pow(2, exposure)
        blender_energy_scale = 1.0 # Reduced from 10.0 to make lights less intense overall

        # --- Determine Light Type and Create Data Block ---
        light_data = None
        bl_light_type = 'POINT' # Default

        # Apply scene_scale^2 to energy for non-directional lights to maintain perceived brightness
        effective_light_power = light_power
        if prim_type != "DistantLight": # Only scale energy for Point/Area/Spot lights
            effective_light_power *= (scene_scale * scene_scale)

        if prim_type == "SphereLight":
            # Check for spotlight shaping attributes
            # USD "shaping:cone:angle" is full angle, Blender "spot_size" is full angle
            # USD "shaping:cone:softness" (0-1) maps to Blender "spot_blend" (0-1)
            is_spotlight = False
            usd_cone_angle = get_attr_value(light_prim, "shaping:cone:angle", 360.0) # Default to wide angle if not specified
            usd_cone_softness = get_attr_value(light_prim, "shaping:cone:softness", 0.0)

            schemas = light_prim.GetAppliedSchemas()
            has_shaping_api = "ShapingAPI" in schemas

            # A SphereLight is treated as a SPOT if ShapingAPI is applied
            # AND its cone angle is meaningfully less than a full sphere (e.g., < 179 degrees).
            # An angle of 179 degrees or wider, even with ShapingAPI, is treated as a point light.
            if has_shaping_api and usd_cone_angle < 179.0:
                is_spotlight = True

            if is_spotlight:
                bl_light_type = 'SPOT'
                light_data = bpy.data.lights.new(name=light_name, type=bl_light_type)
                light_data.energy = effective_light_power * blender_energy_scale
                light_data.shadow_soft_size = radius * scene_scale # Spotlights also have a radius for shadow softness
                
                # Convert USD cone angle (degrees) to Blender spot_size (radians)
                # USD shaping:cone:angle is the full angle of the cone.
                # Blender's spot_size is also the full angle, in radians.
                light_data.spot_size = math.radians(usd_cone_angle)
                light_data.spot_blend = usd_cone_softness # This is a 0-1 value, similar to Blender's
                print(f"  Info: Creating SPOT light for {light_name} (angle: {usd_cone_angle}, softness: {usd_cone_softness})")
            else:
                bl_light_type = 'POINT'
                light_data = bpy.data.lights.new(name=light_name, type=bl_light_type)
                light_data.energy = effective_light_power * blender_energy_scale
                light_data.shadow_soft_size = radius * scene_scale
                print(f"  Info: Creating POINT light for {light_name} (default SphereLight)")

        elif prim_type == "DiskLight":
            bl_light_type = 'AREA'
            light_data = bpy.data.lights.new(name=light_name, type=bl_light_type)
            light_data.shape = 'DISK'
            light_data.energy = effective_light_power * blender_energy_scale # Use scaled power
            light_data.size = radius * 2 * scene_scale # Scale size (diameter)

        elif prim_type == "CylinderLight":
            # Blender has no native cylinder light, approximate with Rect Area light
            bl_light_type = 'AREA'
            light_data = bpy.data.lights.new(name=light_name, type=bl_light_type)
            light_data.shape = 'RECTANGLE'
            light_data.energy = effective_light_power * blender_energy_scale # Use scaled power
            light_data.size = radius * 2 * scene_scale # Scale size X
            light_data.size_y = length * scene_scale   # Scale size Y

        elif prim_type == "RectLight": # USD Rectangular Light
             bl_light_type = 'AREA'
             light_data = bpy.data.lights.new(name=light_name, type=bl_light_type)
             light_data.shape = 'RECTANGLE'
             light_data.energy = effective_light_power * blender_energy_scale # Use scaled power
             light_data.size = get_attr_value(light_prim, "width", 1.0) * scene_scale # Scale size X
             light_data.size_y = get_attr_value(light_prim, "height", 1.0) * scene_scale # Scale size Y

        elif prim_type == "DistantLight": # Directional Light
             bl_light_type = 'SUN'
             light_data = bpy.data.lights.new(name=light_name, type=bl_light_type)
             # Intensity for sun is often treated differently (irradiance)
             # Sun light energy (irradiance) should not be scaled by scene_scale**2
             # Apply blender_energy_scale consistently, but not scene_scale**2 for sun.
             light_data.energy = light_power * blender_energy_scale 
             angle_rad = get_attr_value(light_prim, "angle", 0.53) # Angle in degrees in USD? Assume degrees
             light_data.angle = math.radians(angle_rad) # Blender uses radians

        else:
            print(f"Unsupported light type: {prim_type}. Creating default Point light.")
            light_data = bpy.data.lights.new(name=light_name, type='POINT')
            light_data.energy = effective_light_power * blender_energy_scale # Use scaled power for fallback
            light_data.shadow_soft_size = 0.1 * scene_scale # Also scale default size for fallback Point light

        # --- Apply Common Light Properties ---
        light_data.color = (color[0], color[1], color[2])

        # Color Temperature
        enable_temp = get_attr_value(light_prim, "enableColorTemperature", False)
        if enable_temp:
            color_temp = get_attr_value(light_prim, "colorTemperature", 6500.0)
            if hasattr(light_data, 'use_temperature'):
                light_data.use_temperature = True
                light_data.temperature = color_temp

        # --- Create Light Object ---
        light_obj = bpy.data.objects.new(light_name, light_data)

        # --- Set Transform ---
        light_obj.location = blender_location

        # Convert quaternion rotation (W, X, Y, Z) to Blender Euler
        # Note: This assumes default XYZ Euler order. USD might use a different order.
        light_obj.rotation_mode = 'QUATERNION'
        light_obj.rotation_quaternion = blender_rotation_quat
        # Convert to Euler AFTER setting quaternion if needed for inspection, but keep Quat for accuracy
        light_obj.rotation_euler = light_obj.rotation_quaternion.to_euler('XYZ')

        # Apply scale for Area lights (affects size if shape='SQUARE'/'RECTANGLE')
        # Scaling the object itself is usually not the way to size area lights in Blender
        # We already set size properties on light_data.

        print(f"Created light: {light_name} ({bl_light_type})")
        return light_obj

    except Exception as e:
        import traceback
        print(f"Error creating light from USD prim {light_prim.GetPath()}: {e}")
        traceback.print_exc()
        # Clean up potentially created light data if object creation failed
        if 'light_data' in locals() and light_data and light_data.users == 0:
             bpy.data.lights.remove(light_data)
        return None

def import_lights_from_usd(stage, collection, scene_scale=1.0):
    """
    Import all lights from a USD stage and add them to a collection.

    Args:
        stage: The Usd.Stage to import from.
        collection: The Blender collection to add the lights to.
        scene_scale (float): Global scale factor for the scene.

    Returns:
        list: List of created Blender light objects.
    """
    if not USD_AVAILABLE:
        print("USD libraries not available, cannot import lights.")
        return []

    created_lights = []
    # Define known USD light prim types
    # Exclude geometry lights like DomeLight for now unless specifically handled
    light_types = [
        "SphereLight", "DiskLight", "CylinderLight", "RectLight",
        "DistantLight"
        # "DomeLight", "PortalLight" # Need specific handling
    ]

    print("Searching for lights in stage...")
    # Iterate through all prims in the stage
    for prim in stage.TraverseAll(): # Use TraverseAll to include inactive prims if needed
        prim_type_name = prim.GetTypeName()
        if prim_type_name in light_types:
            print(f"Found light prim: {prim.GetPath()} of type {prim_type_name}")
            light_obj = create_light_from_usd(prim, stage, scene_scale)
            if light_obj:
                try:
                    # Link the light object to the specified collection
                    collection.objects.link(light_obj)
                    created_lights.append(light_obj)
                except Exception as e:
                    print(f"Error linking light '{light_obj.name}' to collection: {e}")
                    # Clean up orphaned light object and data if linking failed
                    if light_obj.data and light_obj.data.users == 1: # Only used by this object
                        bpy.data.lights.remove(light_obj.data)
                    bpy.data.objects.remove(light_obj)

    print(f"Imported {len(created_lights)} lights.")
    return created_lights 
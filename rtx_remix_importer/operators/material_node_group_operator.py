import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty
from .. import material_utils

class MATERIAL_OT_create_aperture_node_group(Operator):
    """Create or update Aperture node group for the selected material"""
    bl_idname = "material.create_aperture_node_group"
    bl_label = "Create Aperture Node Group"
    bl_description = "Convert selected material to use Aperture Opaque or Translucent node group"
    bl_options = {'REGISTER', 'UNDO'}
    
    node_group_type: EnumProperty(
        name="Node Group Type",
        description="Type of Aperture node group to create",
        items=[
            ('OPAQUE', "Aperture Opaque", "Create/use Aperture Opaque node group for standard PBR materials"),
            ('TRANSLUCENT', "Aperture Translucent", "Create/use Aperture Translucent node group for glass/transparent materials"),
        ],
        default='OPAQUE'
    )
    
    preserve_connections: BoolProperty(
        name="Preserve Existing Connections",
        description="Try to preserve existing texture and value connections when converting",
        default=True
    )
    
    force_recreate: BoolProperty(
        name="Force Recreate Node Group",
        description="Force recreation of the node group even if it already exists",
        default=False
    )
    
    @classmethod
    def poll(cls, context):
        # Check if we have an active material
        return (context.active_object is not None and 
                context.active_object.active_material is not None)
    
    def execute(self, context):
        active_material = context.active_object.active_material
        
        if not active_material:
            self.report({'ERROR'}, "No active material found")
            return {'CANCELLED'}
        
        if not active_material.use_nodes:
            active_material.use_nodes = True
            self.report({'INFO'}, f"Enabled nodes for material '{active_material.name}'")
        
        try:
            # Create or get the appropriate node group
            if self.node_group_type == 'TRANSLUCENT':
                if self.force_recreate and material_utils.APERTURE_TRANSLUCENT_NODE_GROUP_NAME in bpy.data.node_groups:
                    bpy.data.node_groups.remove(bpy.data.node_groups[material_utils.APERTURE_TRANSLUCENT_NODE_GROUP_NAME])
                    self.report({'INFO'}, "Removed existing Aperture Translucent node group")
                
                node_group = material_utils.append_aperture_translucent_node_group()
                target_node_group_name = material_utils.APERTURE_TRANSLUCENT_NODE_GROUP_NAME
            else:  # OPAQUE
                if self.force_recreate and material_utils.APERTURE_OPAQUE_NODE_GROUP_NAME in bpy.data.node_groups:
                    bpy.data.node_groups.remove(bpy.data.node_groups[material_utils.APERTURE_OPAQUE_NODE_GROUP_NAME])
                    self.report({'INFO'}, "Removed existing Aperture Opaque node group")
                
                node_group = material_utils.append_aperture_opaque_node_group()
                target_node_group_name = material_utils.APERTURE_OPAQUE_NODE_GROUP_NAME
            
            if not node_group:
                self.report({'ERROR'}, f"Failed to create {target_node_group_name} node group")
                return {'CANCELLED'}
            
            # Convert the material to use the node group
            success = self.convert_material_to_aperture_node_group(
                active_material, 
                node_group, 
                target_node_group_name
            )
            
            if success:
                self.report({'INFO'}, f"Successfully converted '{active_material.name}' to use {target_node_group_name}")
                return {'FINISHED'}
            else:
                self.report({'ERROR'}, f"Failed to convert material '{active_material.name}'")
                return {'CANCELLED'}
                
        except Exception as e:
            self.report({'ERROR'}, f"Error creating node group: {str(e)}")
            return {'CANCELLED'}
    
    def convert_material_to_aperture_node_group(self, material, node_group, node_group_name):
        """Convert a material to use the specified Aperture node group."""
        try:
            nodes = material.node_tree.nodes
            links = material.node_tree.links
            
            # Find the output node
            output_node = None
            for node in nodes:
                if node.type == 'OUTPUT_MATERIAL':
                    output_node = node
                    break
            
            if not output_node:
                # Create output node if it doesn't exist
                output_node = nodes.new(type='ShaderNodeOutputMaterial')
                output_node.location = (400, 0)
            
            # Store existing connections if we want to preserve them
            existing_connections = {}
            if self.preserve_connections:
                existing_connections = self.analyze_existing_material(material)
            
            # Check if material already uses an Aperture node group
            existing_aperture_node = None
            for node in nodes:
                if (node.type == 'GROUP' and 
                    node.node_tree and 
                    node.node_tree.name in [material_utils.APERTURE_OPAQUE_NODE_GROUP_NAME, 
                                          material_utils.APERTURE_TRANSLUCENT_NODE_GROUP_NAME]):
                    existing_aperture_node = node
                    break
            
            # Create new Aperture node group instance
            aperture_node = nodes.new(type='ShaderNodeGroup')
            aperture_node.node_tree = node_group
            aperture_node.name = node_group_name
            aperture_node.location = (0, 0)
            
            # Connect to output
            if 'BSDF' in aperture_node.outputs:
                links.new(aperture_node.outputs['BSDF'], output_node.inputs['Surface'])
            
            if 'Displacement' in aperture_node.outputs:
                links.new(aperture_node.outputs['Displacement'], output_node.inputs['Displacement'])
            
            # Apply preserved connections
            if self.preserve_connections and existing_connections:
                self.apply_preserved_connections(aperture_node, existing_connections, material)
            
            # Remove the old Aperture node if it exists
            if existing_aperture_node and existing_aperture_node != aperture_node:
                nodes.remove(existing_aperture_node)
            
            # Clean up disconnected nodes (optional)
            self.cleanup_disconnected_nodes(material)
            
            return True
            
        except Exception as e:
            print(f"Error converting material: {e}")
            return False
    
    def analyze_existing_material(self, material):
        """Analyze existing material to preserve connections."""
        connections = {}
        nodes = material.node_tree.nodes
        
        # Look for common node types and their values
        for node in nodes:
            if node.type == 'BSDF_PRINCIPLED':
                # Store Principled BSDF values
                connections['base_color'] = self.get_input_value_or_connection(node, 'Base Color')
                connections['metallic'] = self.get_input_value_or_connection(node, 'Metallic')
                connections['roughness'] = self.get_input_value_or_connection(node, 'Roughness')
                connections['ior'] = self.get_input_value_or_connection(node, 'IOR')
                # Handle transmission input (different names in different Blender versions)
                transmission_input = material_utils.get_principled_transmission(node)
                if transmission_input:
                    connections['transmission'] = {
                        'type': 'connection' if transmission_input.is_linked else 'value',
                        'value': transmission_input.default_value if not transmission_input.is_linked else None,
                        'node': transmission_input.links[0].from_node if transmission_input.is_linked else None,
                        'output': transmission_input.links[0].from_socket.name if transmission_input.is_linked else None
                    }
                connections['emission'] = self.get_input_value_or_connection(node, 'Emission')
                connections['normal'] = self.get_input_value_or_connection(node, 'Normal')
                
            elif node.type == 'EMISSION':
                connections['emissive_color'] = self.get_input_value_or_connection(node, 'Color')
                connections['emissive_strength'] = self.get_input_value_or_connection(node, 'Strength')
        
        return connections
    
    def get_input_value_or_connection(self, node, input_name):
        """Get either the default value or connected node info for an input."""
        if input_name in node.inputs:
            input_socket = node.inputs[input_name]
            if input_socket.is_linked:
                # Return info about the connected node
                connected_node = input_socket.links[0].from_node
                connected_output = input_socket.links[0].from_socket.name
                return {
                    'type': 'connection',
                    'node': connected_node,
                    'output': connected_output
                }
            else:
                # Return the default value
                return {
                    'type': 'value',
                    'value': input_socket.default_value
                }
        return None
    
    def apply_preserved_connections(self, aperture_node, connections, material):
        """Apply preserved connections to the new Aperture node group."""
        links = material.node_tree.links
        
        # Map old connections to new Aperture node inputs
        mapping = {
            'base_color': 'Base Color' if self.node_group_type == 'OPAQUE' else 'Transmittance/Diffuse Albedo',
            'metallic': 'Metallic',
            'roughness': 'Roughness', 
            'ior': 'IOR',
            'emissive_color': 'Emissive Color',
            'normal': 'Normal Map'
        }
        
        # Special handling for translucent materials
        if self.node_group_type == 'TRANSLUCENT':
            # If transmission was > 0, enable some translucent features
            transmission_info = connections.get('transmission')
            if transmission_info and transmission_info['type'] == 'value':
                if transmission_info['value'] > 0.1:
                    # Set some reasonable defaults for translucent behavior
                    if 'Thin Walled' in aperture_node.inputs:
                        aperture_node.inputs['Thin Walled'].default_value = 0.0
                    if 'Use Diffuse Layer' in aperture_node.inputs:
                        aperture_node.inputs['Use Diffuse Layer'].default_value = 0.0
            
            # Handle emission
            emission_info = connections.get('emission')
            emissive_color_info = connections.get('emissive_color')
            emissive_strength_info = connections.get('emissive_strength')
            
            if emission_info or emissive_color_info or emissive_strength_info:
                if 'Enable Emission' in aperture_node.inputs:
                    aperture_node.inputs['Enable Emission'].default_value = 1.0
        
        # Apply the mapped connections
        for old_key, new_input_name in mapping.items():
            if old_key in connections and new_input_name in aperture_node.inputs:
                connection_info = connections[old_key]
                
                if connection_info['type'] == 'connection':
                    # Reconnect the node
                    connected_node = connection_info['node']
                    connected_output = connection_info['output']
                    if connected_output in connected_node.outputs:
                        links.new(
                            connected_node.outputs[connected_output],
                            aperture_node.inputs[new_input_name]
                        )
                elif connection_info['type'] == 'value':
                    # Set the value
                    try:
                        aperture_node.inputs[new_input_name].default_value = connection_info['value']
                    except (TypeError, ValueError):
                        # Handle cases where value types don't match
                        pass
    
    def cleanup_disconnected_nodes(self, material):
        """Remove nodes that are no longer connected to the output."""
        nodes = material.node_tree.nodes
        
        # Find all nodes connected to the output
        connected_nodes = set()
        output_nodes = [n for n in nodes if n.type == 'OUTPUT_MATERIAL']
        
        for output_node in output_nodes:
            self.trace_connected_nodes(output_node, connected_nodes)
        
        # Remove disconnected nodes (except input nodes like Image Texture which might be reused)
        nodes_to_remove = []
        for node in nodes:
            if (node not in connected_nodes and 
                node.type not in ['TEX_IMAGE', 'TEX_NOISE', 'TEX_VORONOI', 'TEX_WAVE', 'TEX_MUSGRAVE'] and
                not node.type.startswith('GROUP')):
                nodes_to_remove.append(node)
        
        for node in nodes_to_remove:
            nodes.remove(node)
    
    def trace_connected_nodes(self, node, connected_nodes):
        """Recursively trace all nodes connected to the given node."""
        if node in connected_nodes:
            return
        
        connected_nodes.add(node)
        
        # Trace backward through input connections
        for input_socket in node.inputs:
            if input_socket.is_linked:
                for link in input_socket.links:
                    self.trace_connected_nodes(link.from_node, connected_nodes)


class MATERIAL_OT_convert_to_aperture_translucent(Operator):
    """Quick operator to convert selected material to Aperture Translucent"""
    bl_idname = "material.convert_to_aperture_translucent"
    bl_label = "Convert to Aperture Translucent"
    bl_description = "Convert the selected material to use Aperture Translucent node group"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and 
                context.active_object.active_material is not None)
    
    def execute(self, context):
        # Use the main operator with translucent settings
        bpy.ops.material.create_aperture_node_group(
            node_group_type='TRANSLUCENT',
            preserve_connections=True,
            force_recreate=False
        )
        return {'FINISHED'}


class MATERIAL_OT_convert_to_aperture_opaque(Operator):
    """Quick operator to convert selected material to Aperture Opaque"""
    bl_idname = "material.convert_to_aperture_opaque"
    bl_label = "Convert to Aperture Opaque"
    bl_description = "Convert the selected material to use Aperture Opaque node group"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and 
                context.active_object.active_material is not None)
    
    def execute(self, context):
        # Use the main operator with opaque settings
        bpy.ops.material.create_aperture_node_group(
            node_group_type='OPAQUE',
            preserve_connections=True,
            force_recreate=False
        )
        return {'FINISHED'}


def register():
    bpy.utils.register_class(MATERIAL_OT_create_aperture_node_group)
    bpy.utils.register_class(MATERIAL_OT_convert_to_aperture_translucent)
    bpy.utils.register_class(MATERIAL_OT_convert_to_aperture_opaque)


def unregister():
    bpy.utils.unregister_class(MATERIAL_OT_convert_to_aperture_opaque)
    bpy.utils.unregister_class(MATERIAL_OT_convert_to_aperture_translucent)
    bpy.utils.unregister_class(MATERIAL_OT_create_aperture_node_group) 
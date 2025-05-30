"""
Operator for demonstrating parallel texture processing capabilities.
"""

import bpy
import asyncio
import os
import tempfile
from bpy.types import Operator
from bpy.props import BoolProperty, StringProperty, IntProperty
from bpy_extras.io_utils import ExportHelper


class REMIX_OT_ParallelTextureProcessor(Operator):
    """Process textures in parallel using the queue system"""
    bl_idname = "remix.parallel_texture_processor"
    bl_label = "Parallel Texture Processor"
    bl_description = "Process multiple textures in parallel for faster conversion"
    bl_options = {'REGISTER', 'UNDO'}
    
    selected_only: BoolProperty(
        name="Selected Objects Only",
        description="Only process textures from selected objects",
        default=False
    )
    
    max_workers: IntProperty(
        name="Max Workers",
        description="Maximum number of parallel workers",
        default=4,
        min=1,
        max=16
    )
    
    timeout: IntProperty(
        name="Timeout (seconds)",
        description="Maximum time to wait for all conversions",
        default=300,
        min=30,
        max=3600
    )
    
    def execute(self, context):
        try:
            from ..core_utils import get_texture_processor, get_texture_queue
            
            texture_processor = get_texture_processor()
            
            if not texture_processor.is_available():
                self.report({'ERROR'}, "texconv.exe not found. Cannot process textures.")
                return {'CANCELLED'}
            
            # Collect textures to process
            textures_to_process = []
            
            objects_to_check = context.selected_objects if self.selected_only else bpy.data.objects
            
            for obj in objects_to_check:
                if obj.type == 'MESH' and obj.data.materials:
                    for material in obj.data.materials:
                        if material and material.use_nodes:
                            for node in material.node_tree.nodes:
                                if node.type == 'TEX_IMAGE' and node.image:
                                    bl_image = node.image
                                    texture_type = self._determine_texture_type(node)
                                    textures_to_process.append((bl_image, texture_type))
            
            if not textures_to_process:
                self.report({'INFO'}, "No textures found to process")
                return {'FINISHED'}
            
            # Remove duplicates
            unique_textures = {}
            for bl_image, texture_type in textures_to_process:
                if bl_image.name not in unique_textures:
                    unique_textures[bl_image.name] = (bl_image, texture_type)
            
            textures_to_process = list(unique_textures.values())
            
            self.report({'INFO'}, f"Starting parallel processing of {len(textures_to_process)} textures...")
            
            # Create temporary output directory
            temp_dir = tempfile.mkdtemp(prefix="remix_parallel_")
            
            def progress_callback(message):
                print(f"[Parallel Processor] {message}")
            
            async def process_textures():
                """Process textures using the parallel queue system."""
                # Prepare tasks
                tasks = []
                for bl_image, texture_type in textures_to_process:
                    base_name = os.path.splitext(bl_image.name)[0]
                    suffix = texture_processor.get_texture_suffix(texture_type)
                    output_path = os.path.join(temp_dir, f"{base_name}{suffix}.dds")
                    
                    tasks.append((bl_image, output_path, texture_type, None))
                
                # Process in parallel
                results = await texture_processor.process_textures_parallel(
                    tasks,
                    progress_callback=progress_callback,
                    timeout=self.timeout
                )
                
                return results
            
            # Run the processing
            try:
                results = asyncio.run(process_textures())
                
                # Count successful conversions
                successful = sum(1 for success in results.values() if success)
                total = len(results)
                
                # Show queue status
                queue_status = texture_processor.get_queue_status()
                progress_callback(f"Queue status: {queue_status}")
                
                self.report({'INFO'}, f"Parallel processing completed: {successful}/{total} textures successful")
                
                # Show detailed results
                for task_id, success in results.items():
                    task_status = texture_processor.get_conversion_status(task_id)
                    if task_status:
                        status_msg = "SUCCESS" if success else f"FAILED: {task_status.get('error', 'Unknown error')}"
                        print(f"  {task_status.get('texture_name', 'Unknown')}: {status_msg}")
                
            except Exception as e:
                self.report({'ERROR'}, f"Error during parallel processing: {e}")
                return {'CANCELLED'}
            
            finally:
                # Cleanup
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                except:
                    pass
            
            return {'FINISHED'}
            
        except ImportError as e:
            self.report({'ERROR'}, f"Required modules not available: {e}")
            return {'CANCELLED'}
    
    def _determine_texture_type(self, node):
        """Determine texture type from node."""
        node_name = (node.label or node.name).lower()
        image_name = node.image.name.lower() if node.image else ""
        
        # Check naming patterns
        if any(term in node_name or term in image_name for term in ['normal', 'norm', 'nrm']):
            return 'normal'
        elif any(term in node_name or term in image_name for term in ['rough', 'roughness']):
            return 'roughness'
        elif any(term in node_name or term in image_name for term in ['metal', 'metallic']):
            return 'metallic'
        elif any(term in node_name or term in image_name for term in ['emit', 'emission', 'emissive']):
            return 'emission'
        elif any(term in node_name or term in image_name for term in ['opacity', 'alpha']):
            return 'opacity'
        
        # Check connections
        if node.outputs and node.outputs[0].is_linked:
            for link in node.outputs[0].links:
                socket_name = link.to_socket.name.lower()
                
                if 'normal' in socket_name:
                    return 'normal'
                elif 'rough' in socket_name:
                    return 'roughness'
                elif 'metal' in socket_name:
                    return 'metallic'
                elif any(term in socket_name for term in ['emit', 'emission']):
                    return 'emission'
                elif 'alpha' in socket_name:
                    return 'opacity'
        
        return 'base color'


class REMIX_OT_QueueStatus(Operator):
    """Show texture processing queue status"""
    bl_idname = "remix.queue_status"
    bl_label = "Show Queue Status"
    bl_description = "Display current texture processing queue status"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        try:
            from ..core_utils import get_texture_processor
            
            texture_processor = get_texture_processor()
            queue_status = texture_processor.get_queue_status()
            
            status_message = (
                f"Queue Status:\n"
                f"  Running: {queue_status['is_running']}\n"
                f"  Queue Size: {queue_status['queue_size']}\n"
                f"  Active Tasks: {queue_status['active_tasks']}\n"
                f"  Completed Tasks: {queue_status['completed_tasks']}\n"
                f"  Max Concurrent: {queue_status['max_concurrent']}\n"
                f"  Workers: {queue_status['worker_count']}"
            )
            
            print(status_message)
            self.report({'INFO'}, "Queue status printed to console")
            
            return {'FINISHED'}
            
        except ImportError:
            self.report({'ERROR'}, "Texture processing tools not available")
            return {'CANCELLED'}


class REMIX_OT_ClearQueue(Operator):
    """Clear completed tasks from the texture processing queue"""
    bl_idname = "remix.clear_queue"
    bl_label = "Clear Queue"
    bl_description = "Clear completed tasks from the texture processing queue"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        try:
            from ..core_utils import get_texture_queue
            
            queue = get_texture_queue()
            
            # Clear completed tasks
            completed_count = len(queue.completed_tasks)
            queue.completed_tasks.clear()
            
            self.report({'INFO'}, f"Cleared {completed_count} completed tasks from queue")
            
            return {'FINISHED'}
            
        except ImportError:
            self.report({'ERROR'}, "Texture processing tools not available")
            return {'CANCELLED'}


# Registration
classes = [
    REMIX_OT_ParallelTextureProcessor,
    REMIX_OT_QueueStatus,
    REMIX_OT_ClearQueue,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls) 
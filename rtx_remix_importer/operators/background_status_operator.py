"""
Operators for managing background texture processing jobs.
"""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty


class REMIX_OT_BackgroundJobStatus(Operator):
    """Show status of background texture processing jobs"""
    bl_idname = "remix.background_job_status"
    bl_label = "Background Job Status"
    bl_description = "Show status of background texture processing jobs"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        from .. import core_utils
        background_processor = core_utils.get_background_processor()
        
        # Show active jobs
        if background_processor.active_jobs:
            self.report({'INFO'}, f"Active jobs: {len(background_processor.active_jobs)}")
            for job_id, job_info in background_processor.active_jobs.items():
                status = background_processor.get_job_status(job_id)
                if status:
                    progress_pct = (status['progress'] / status['total']) * 100 if status['total'] > 0 else 0
                    elapsed = status['elapsed']
                    print(f"  {job_id}: {status['status']} - {status['progress']}/{status['total']} ({progress_pct:.1f}%) - {elapsed:.1f}s")
        else:
            self.report({'INFO'}, "No active background jobs")
            
        # Show completed jobs
        if background_processor.completed_jobs:
            print(f"Completed jobs: {len(background_processor.completed_jobs)}")
            for job_id, job_info in background_processor.completed_jobs.items():
                print(f"  {job_id}: {job_info['status']}")
        
        return {'FINISHED'}


class REMIX_OT_CancelBackgroundJob(Operator):
    """Cancel a background texture processing job"""
    bl_idname = "remix.cancel_background_job"
    bl_label = "Cancel Background Job"
    bl_description = "Cancel a specific background texture processing job"
    bl_options = {'REGISTER'}
    
    job_id: StringProperty(
        name="Job ID",
        description="ID of the job to cancel"
    )
    
    def execute(self, context):
        if not self.job_id:
            self.report({'ERROR'}, "No job ID specified")
            return {'CANCELLED'}
            
        from .. import core_utils
        background_processor = core_utils.get_background_processor()
        
        if background_processor.cancel_job(self.job_id):
            self.report({'INFO'}, f"Cancelled job: {self.job_id}")
        else:
            self.report({'WARNING'}, f"Job not found or already completed: {self.job_id}")
            
        return {'FINISHED'}


class REMIX_OT_CancelAllBackgroundJobs(Operator):
    """Cancel all active background texture processing jobs"""
    bl_idname = "remix.cancel_all_background_jobs"
    bl_label = "Cancel All Background Jobs"
    bl_description = "Cancel all active background texture processing jobs"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        from .. import core_utils
        background_processor = core_utils.get_background_processor()
        
        cancelled_count = 0
        for job_id in list(background_processor.active_jobs.keys()):
            if background_processor.cancel_job(job_id):
                cancelled_count += 1
        
        if cancelled_count > 0:
            self.report({'INFO'}, f"Cancelled {cancelled_count} background jobs")
        else:
            self.report({'INFO'}, "No active jobs to cancel")
            
        return {'FINISHED'}


class REMIX_OT_CleanupCompletedJobs(Operator):
    """Clean up completed background jobs"""
    bl_idname = "remix.cleanup_completed_jobs"
    bl_label = "Cleanup Completed Jobs"
    bl_description = "Remove completed background jobs from memory"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        from .. import core_utils
        background_processor = core_utils.get_background_processor()
        
        completed_count = len(background_processor.completed_jobs)
        background_processor.cleanup_completed_jobs()
        
        if completed_count > 0:
            self.report({'INFO'}, f"Cleaned up {completed_count} completed jobs")
        else:
            self.report({'INFO'}, "No completed jobs to clean up")
            
        return {'FINISHED'}


class REMIX_OT_BackgroundProcessingTest(Operator):
    """Test background texture processing with dummy textures"""
    bl_idname = "remix.background_processing_test"
    bl_label = "Test Background Processing"
    bl_description = "Test the background texture processing system with dummy data"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        # Create some dummy texture tasks for testing
        dummy_tasks = []
        
        # Find some textures in the scene to test with
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.material_slots:
                for slot in obj.material_slots:
                    if slot.material and slot.material.use_nodes:
                        for node in slot.material.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                # Create a dummy task
                                dummy_tasks.append((
                                    node.image,
                                    f"/tmp/test_{node.image.name}.dds",
                                    "base color",
                                    "BC7_UNORM_SRGB"
                                ))
                                if len(dummy_tasks) >= 3:  # Limit to 3 for testing
                                    break
                    if len(dummy_tasks) >= 3:
                        break
            if len(dummy_tasks) >= 3:
                break
        
        if not dummy_tasks:
            self.report({'WARNING'}, "No textures found in scene for testing")
            return {'CANCELLED'}
        
        from .. import core_utils
        background_processor = core_utils.get_background_processor()
        
        def progress_callback(msg):
            print(f"TEST: {msg}")
        
        def completion_callback(job_id, job_info):
            print(f"TEST: Job {job_id} completed with status: {job_info['status']}")
        
        job_id = background_processor.start_background_job(
            dummy_tasks,
            progress_callback=progress_callback,
            completion_callback=completion_callback
        )
        
        self.report({'INFO'}, f"Started test background job: {job_id}")
        return {'FINISHED'} 
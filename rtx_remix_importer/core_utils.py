import bpy
import os
import math
import hashlib
import json
import tempfile
import subprocess
import asyncio
import threading
import time
from typing import Optional, Tuple, Dict, Any, List, Callable
from concurrent.futures import ThreadPoolExecutor

try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

from . import constants

# Global thread pool for async operations
_thread_pool = None
_texture_queue = None

def get_thread_pool():
    """Get or create the global thread pool for async operations."""
    global _thread_pool
    if _thread_pool is None:
        # Use CPU count for optimal parallel processing
        # Each worker will run its own texconv process, so we want one per core
        cpu_count = os.cpu_count() or 4
        max_workers = min(cpu_count, 8)  # Cap at 8 to prevent resource exhaustion
        _thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        print(f"[TextureProcessor] Initialized thread pool with {max_workers} workers for {cpu_count} CPU cores")
    return _thread_pool

def get_texture_queue():
    """Get or create the global texture processing queue."""
    global _texture_queue
    if _texture_queue is None:
        _texture_queue = TextureProcessingQueue()
    return _texture_queue

def cleanup_thread_pool():
    """Cleanup the global thread pool and texture queue."""
    global _thread_pool, _texture_queue
    if _texture_queue is not None:
        _texture_queue.shutdown()
        _texture_queue = None
    if _thread_pool is not None:
        _thread_pool.shutdown(wait=True)
        _thread_pool = None

# --- Path Utilities ---

def get_relative_path(from_path: str, to_path: str) -> str:
    """Calculates the relative path from one file to another with proper cross-platform handling."""
    try:
        from_dir = os.path.dirname(from_path)
        abs_from_path = os.path.abspath(from_dir)
        abs_to_path = os.path.abspath(to_path)
        rel_path = os.path.relpath(abs_to_path, start=abs_from_path)
        
        # Normalize path separators for USD (always forward slashes)
        rel_path = rel_path.replace('\\', '/')
        
        # Ensure relative paths start with './' for clarity
        if not rel_path.startswith("../") and not rel_path.startswith("./") and rel_path != ".":
            rel_path = "./" + rel_path
            
        return rel_path
    except Exception as e:
        print(f"Error calculating relative path from '{from_path}' to '{to_path}': {e}")
        return to_path

def sanitize_prim_name(name: str) -> str:
    """Sanitizes a name for use as a USD prim name."""
    invalid_chars = " .!@#$%^&*()-+=[]{};:'\"<>,/?`~"
    sanitized = name
    for char in invalid_chars:
        sanitized = sanitized.replace(char, '_')
    
    # Ensure it starts with a letter or underscore
    if not sanitized or not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = '_' + sanitized
    return sanitized

def generate_uuid_name(name: str, prefix: str = "ref_") -> str:
    """Generates a UUID-style name based on the input name for RTX Remix compatibility."""
    import uuid
    uuid_seed = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    return f"{prefix}{uuid_seed.hex[:32]}"

# --- Material Utilities ---

class MaterialPathResolver:
    """Handles material path resolution and caching."""
    
    def __init__(self, usd_file_path_context: str):
        self.usd_file_path_context = usd_file_path_context
        self._texture_dir_cache = {}
    
    def find_texture_dir(self) -> Optional[str]:
        """Find texture directory with caching."""
        if self.usd_file_path_context in self._texture_dir_cache:
            return self._texture_dir_cache[self.usd_file_path_context]
        
        usd_dir = os.path.dirname(self.usd_file_path_context)
        mod_dir = os.path.dirname(usd_dir)
        mod_root_dir = os.path.dirname(mod_dir)

        potential_dirs = [
            os.path.join(mod_dir, "captures", "textures"),
            os.path.join(mod_dir, "assets", "textures"),
            os.path.join(mod_dir, "assets"),
            os.path.join(mod_root_dir, "assets", "textures"),
            os.path.join(mod_root_dir, "assets"),
            os.path.join(mod_root_dir, "textures"),
        ]

        for p_dir in potential_dirs:
            if os.path.isdir(p_dir):
                self._texture_dir_cache[self.usd_file_path_context] = p_dir
                return p_dir
        
        self._texture_dir_cache[self.usd_file_path_context] = None
        return None

def create_material_cache_key(base_material_path: str, instance_metadata: Dict[str, Any]) -> str:
    """Create a unique cache key for materials with metadata."""
    metadata_hash = ""
    if instance_metadata:
        sorted_meta_string = json.dumps(instance_metadata, sort_keys=True)
        metadata_hash = hashlib.md5(sorted_meta_string.encode('utf-8')).hexdigest()[:8]
    
    return f"{base_material_path}_{metadata_hash}" if metadata_hash else base_material_path

# --- USD Stage Utilities ---

class USDStageManager:
    """Manages USD stage operations with proper resource cleanup."""
    
    def __init__(self):
        self._open_stages = {}
    
    def get_or_open_stage(self, file_path: str) -> Optional[Usd.Stage]:
        """Get or open a USD stage with caching."""
        if not USD_AVAILABLE:
            return None
        
        abs_path = os.path.abspath(file_path)
        if abs_path in self._open_stages:
            return self._open_stages[abs_path]
        
        try:
            stage = Usd.Stage.Open(abs_path, Usd.Stage.LoadAll)
            if stage:
                self._open_stages[abs_path] = stage
                return stage
        except Exception as e:
            print(f"Error opening USD stage '{abs_path}': {e}")
        
        return None
    
    def close_stage(self, file_path: str):
        """Close and remove a stage from cache."""
        abs_path = os.path.abspath(file_path)
        if abs_path in self._open_stages:
            # USD stages don't have explicit close, just remove from cache
            del self._open_stages[abs_path]
    
    def cleanup_all(self):
        """Cleanup all cached stages."""
        self._open_stages.clear()

# Global stage manager instance
_stage_manager = None

def get_stage_manager():
    """Get or create the global stage manager."""
    global _stage_manager
    if _stage_manager is None:
        _stage_manager = USDStageManager()
    return _stage_manager

# --- Async Texture Processing Queue ---

class TextureTask:
    """Represents a texture processing task."""
    
    def __init__(self, task_id: str, bl_image: bpy.types.Image, output_path: str, 
                 texture_type: str = 'base color', dds_format: Optional[str] = None,
                 progress_callback: Optional[Callable[[str], None]] = None):
        self.task_id = task_id
        self.bl_image = bl_image
        self.output_path = output_path
        self.texture_type = texture_type
        self.dds_format = dds_format
        self.progress_callback = progress_callback
        self.future = None
        self.status = "pending"  # pending, processing, completed, failed
        self.result = None
        self.error = None
        self.created_at = asyncio.get_event_loop().time()

class TextureProcessingQueue:
    """Manages a queue of texture processing tasks with parallel execution and batching."""
    
    def __init__(self, max_concurrent_tasks: int = None, batch_size: int = None):
        # Use CPU count for optimal parallel processing
        cpu_count = os.cpu_count() or 4
        self.max_concurrent_tasks = max_concurrent_tasks or min(cpu_count, 8)
        self.batch_size = batch_size or 4  # Textures per batch
        
        self.queue = asyncio.Queue()
        self.active_tasks = {}  # task_id -> TextureTask
        self.completed_tasks = {}  # task_id -> TextureTask
        
        # Use a semaphore that allows one texconv process per CPU core
        self.processing_semaphore = asyncio.Semaphore(self.max_concurrent_tasks)
        self.worker_tasks = []
        self.is_running = False
        self._task_counter = 0
        self._lock = asyncio.Lock()
        
        # Batching state
        self._pending_batch = []
        self._batch_timer = None
        self._batch_timeout = 0.5  # Seconds to wait before processing incomplete batch
    
    async def start(self):
        """Start the queue processing workers."""
        if self.is_running:
            return
        
        self.is_running = True
        # Create worker tasks - one per CPU core for optimal texconv utilization
        for i in range(self.max_concurrent_tasks):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.worker_tasks.append(worker)
    
    async def stop(self):
        """Stop the queue processing workers."""
        self.is_running = False
        
        # Cancel batch timer if running
        if self._batch_timer:
            self._batch_timer.cancel()
        
        # Process any remaining batch
        if self._pending_batch:
            await self._process_pending_batch()
        
        # Cancel all worker tasks
        for worker in self.worker_tasks:
            worker.cancel()
        
        # Wait for workers to finish
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        
        self.worker_tasks.clear()
    
    def shutdown(self):
        """Synchronous shutdown for cleanup."""
        if self.is_running:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule shutdown for later
                    asyncio.create_task(self.stop())
                else:
                    loop.run_until_complete(self.stop())
            except:
                pass  # Best effort cleanup
    
    async def add_task(self, bl_image: bpy.types.Image, output_path: str, 
                      texture_type: str = 'base color', dds_format: Optional[str] = None,
                      progress_callback: Optional[Callable[[str], None]] = None) -> str:
        """Add a texture processing task to the queue."""
        async with self._lock:
            self._task_counter += 1
            task_id = f"texture_task_{self._task_counter}"
        
        task = TextureTask(task_id, bl_image, output_path, texture_type, dds_format, progress_callback)
        
        # Ensure workers are running
        if not self.is_running:
            await self.start()
        
        # Add to pending batch instead of directly to queue
        await self._add_to_batch(task)
        
        if progress_callback:
            progress_callback(f"Task {task_id} queued for batch processing")
        
        return task_id
    
    async def _add_to_batch(self, task: TextureTask):
        """Add task to pending batch and process when batch is full."""
        self._pending_batch.append(task)
        self.active_tasks[task.task_id] = task
        
        # If batch is full, process it immediately
        if len(self._pending_batch) >= self.batch_size:
            await self._process_pending_batch()
        else:
            # Start/restart timer for incomplete batch
            if self._batch_timer:
                self._batch_timer.cancel()
            self._batch_timer = asyncio.create_task(self._batch_timeout_handler())
    
    async def _batch_timeout_handler(self):
        """Handle batch timeout - process incomplete batch after timeout."""
        try:
            await asyncio.sleep(self._batch_timeout)
            if self._pending_batch:
                await self._process_pending_batch()
        except asyncio.CancelledError:
            pass
    
    async def _process_pending_batch(self):
        """Process the current pending batch."""
        if not self._pending_batch:
            return
        
        # Create batch from pending tasks
        batch = self._pending_batch.copy()
        self._pending_batch.clear()
        
        # Cancel timer
        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None
        
        # Add batch to processing queue
        await self.queue.put(batch)
    
    async def add_batch(self, tasks: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
                       progress_callback: Optional[Callable[[str], None]] = None) -> List[str]:
        """Add multiple texture processing tasks to the queue."""
        task_ids = []
        
        for bl_image, output_path, texture_type, dds_format in tasks:
            task_id = await self.add_task(bl_image, output_path, texture_type, dds_format, progress_callback)
            task_ids.append(task_id)
        
        # Force process any remaining batch
        if self._pending_batch:
            await self._process_pending_batch()
        
        if progress_callback:
            progress_callback(f"Added {len(task_ids)} tasks to processing queue")
        
        return task_ids
    
    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a specific task to complete."""
        start_time = asyncio.get_event_loop().time()
        
        while task_id in self.active_tasks:
            if timeout and (asyncio.get_event_loop().time() - start_time) > timeout:
                return False
            
            await asyncio.sleep(0.1)
        
        return task_id in self.completed_tasks
    
    async def wait_for_all(self, task_ids: List[str], timeout: Optional[float] = None,
                          progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Wait for all specified tasks to complete."""
        start_time = asyncio.get_event_loop().time()
        
        while True:
            remaining_tasks = [tid for tid in task_ids if tid in self.active_tasks]
            
            if not remaining_tasks:
                break
            
            if timeout and (asyncio.get_event_loop().time() - start_time) > timeout:
                if progress_callback:
                    progress_callback(f"Timeout waiting for {len(remaining_tasks)} tasks")
                return False
            
            if progress_callback:
                completed = len(task_ids) - len(remaining_tasks)
                progress_callback(f"Completed {completed}/{len(task_ids)} texture tasks")
            
            await asyncio.sleep(0.5)
        
        if progress_callback:
            progress_callback(f"All {len(task_ids)} texture tasks completed")
        
        return True
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a specific task."""
        if task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            return {
                "task_id": task_id,
                "status": task.status,
                "texture_name": task.bl_image.name,
                "output_path": task.output_path,
                "texture_type": task.texture_type,
                "error": task.error
            }
        elif task_id in self.completed_tasks:
            task = self.completed_tasks[task_id]
            return {
                "task_id": task_id,
                "status": task.status,
                "texture_name": task.bl_image.name,
                "output_path": task.output_path,
                "texture_type": task.texture_type,
                "result": task.result,
                "error": task.error
            }
        
        return None
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get overall queue status."""
        return {
            "is_running": self.is_running,
            "queue_size": self.queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "completed_tasks": len(self.completed_tasks),
            "max_concurrent": self.max_concurrent_tasks,
            "worker_count": len(self.worker_tasks),
            "batch_size": self.batch_size,
            "pending_batch_size": len(self._pending_batch)
        }
    
    async def _worker(self, worker_name: str):
        """Worker coroutine that processes batches of tasks from the queue."""
        texture_processor = get_texture_processor()
        
        while self.is_running:
            try:
                # Get batch from queue with timeout
                batch = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                
                # Acquire semaphore for this texconv process
                async with self.processing_semaphore:
                    if not batch:
                        continue
                    
                    # Update all tasks in batch to processing status
                    for task in batch:
                        task.status = "processing"
                        if task.progress_callback:
                            task.progress_callback(f"[{worker_name}] Processing batch with {task.bl_image.name}")
                    
                    try:
                        # Prepare batch data for texconv
                        batch_data = []
                        for task in batch:
                            batch_data.append((task.bl_image, task.output_path, task.texture_type, task.dds_format))
                        
                        # Process entire batch with single texconv call
                        batch_results = await texture_processor.convert_texture_batch_async(
                            batch_data,
                            progress_callback=lambda msg: self._batch_progress_callback(batch, msg)
                        )
                        
                        # Update individual task results
                        for i, task in enumerate(batch):
                            if i < len(batch_results):
                                task.result = batch_results[i]
                                task.status = "completed" if batch_results[i] else "failed"
                                if not batch_results[i]:
                                    task.error = "Batch conversion failed"
                            else:
                                task.result = False
                                task.status = "failed"
                                task.error = "Batch processing error"
                        
                    except Exception as e:
                        # Mark all tasks in batch as failed
                        for task in batch:
                            task.status = "failed"
                            task.error = str(e)
                            if task.progress_callback:
                                task.progress_callback(f"Error processing batch with {task.bl_image.name}: {e}")
                    
                    finally:
                        # Move all tasks from active to completed
                        for task in batch:
                            if task.task_id in self.active_tasks:
                                del self.active_tasks[task.task_id]
                            self.completed_tasks[task.task_id] = task
                            
                            if task.progress_callback:
                                status_msg = "completed successfully" if task.status == "completed" else f"failed: {task.error}"
                                task.progress_callback(f"[{worker_name}] Task {task.task_id} {status_msg}")
                        
                        self.queue.task_done()
            
            except asyncio.TimeoutError:
                # No batches in queue, continue
                continue
            except asyncio.CancelledError:
                # Worker was cancelled
                break
            except Exception as e:
                print(f"Worker {worker_name} error: {e}")
                continue
    
    def _batch_progress_callback(self, batch: List[TextureTask], message: str):
        """Forward progress messages to all tasks in a batch."""
        for task in batch:
            if task.progress_callback:
                task.progress_callback(message)

# --- Async Texture Processing ---

class TextureProcessor:
    """Handles texture processing operations asynchronously."""
    
    def __init__(self, texconv_path: Optional[str] = None):
        self.texconv_path = texconv_path or self._find_texconv()
        self._format_map = {
            'BC1_UNORM': 'BC1_UNORM',
            'BC1_UNORM_SRGB': 'BC1_UNORM_SRGB',
            'BC3_UNORM': 'BC3_UNORM',
            'BC3_UNORM_SRGB': 'BC3_UNORM_SRGB',
            'BC4_UNORM': 'BC4_UNORM',
            'BC4_SNORM': 'BC4_SNORM',
            'BC5_UNORM': 'BC5_UNORM',
            'BC5_SNORM': 'BC5_SNORM',
            'BC7_UNORM': 'BC7_UNORM',
            'BC7_UNORM_SRGB': 'BC7_UNORM_SRGB',
        }
        
        # RTX Remix texture type suffixes
        self.texture_type_suffixes = {
            'base color': ".a.rtex",
            'albedo': ".a.rtex",
            'normal': ".n.rtex", 
            'roughness': ".r.rtex",
            'metallic': ".m.rtex",
            'emission': ".e.rtex",
            'emissive': ".e.rtex",
            'opacity': ".o.rtex",
            'specular': ".s.rtex"
        }
        
        # DDS format recommendations by texture type
        self.format_recommendations = {
            'base color': 'BC7_UNORM_SRGB',
            'albedo': 'BC7_UNORM_SRGB',
            'normal': 'BC5_UNORM',
            'roughness': 'BC4_UNORM',
            'metallic': 'BC4_UNORM',
            'emission': 'BC7_UNORM_SRGB',
            'emissive': 'BC7_UNORM_SRGB',
            'opacity': 'BC4_UNORM',
            'specular': 'BC4_UNORM'
        }
        
        # Batching configuration
        self.batch_size = 4  # Number of textures to process per texconv call
        self.max_concurrent_processes = min(os.cpu_count() or 4, 8)  # One per CPU core, max 8
    
    def _find_texconv(self) -> Optional[str]:
        """Find texconv.exe in the addon directory."""
        addon_dir = os.path.dirname(__file__)
        potential_path = os.path.join(addon_dir, "texconv", "texconv.exe")
        return potential_path if os.path.exists(potential_path) else None
    
    def is_available(self) -> bool:
        """Check if texture processing is available."""
        return self.texconv_path is not None
    
    def get_recommended_format(self, texture_type: str) -> str:
        """Get recommended DDS format for texture type."""
        return self.format_recommendations.get(texture_type.lower(), 'BC7_UNORM_SRGB')
    
    def get_texture_suffix(self, texture_type: str) -> str:
        """Get RTX Remix suffix for texture type."""
        return self.texture_type_suffixes.get(texture_type.lower(), "")
    
    # --- Batch Processing Methods ---
    
    async def convert_texture_batch_async(
        self,
        texture_batch: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[bool]:
        """Convert a batch of textures using a single texconv process.
        
        Args:
            texture_batch: List of (bl_image, output_path, texture_type, dds_format) tuples
            progress_callback: Optional progress callback function
            
        Returns:
            List of success status for each texture in the batch
        """
        if not self.is_available() or not texture_batch:
            return [False] * len(texture_batch)
        
        def _convert_batch():
            return self._convert_texture_batch_sync(texture_batch, progress_callback)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(get_thread_pool(), _convert_batch)
    
    def _convert_texture_batch_sync(
        self,
        texture_batch: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[bool]:
        """Convert a batch of textures synchronously using a single texconv process."""
        if not texture_batch:
            return []
        
        results = [False] * len(texture_batch)
        temp_files = []
        
        try:
            if progress_callback:
                progress_callback(f"Processing batch of {len(texture_batch)} textures...")
            
            # First, check which textures already exist and skip them
            textures_to_process = []
            for i, (bl_image, output_path, texture_type, dds_format) in enumerate(texture_batch):
                if os.path.exists(output_path):
                    if progress_callback:
                        progress_callback(f"Skipping existing texture: {os.path.basename(output_path)}")
                    results[i] = True  # Mark as successful since file exists
                else:
                    textures_to_process.append((i, bl_image, output_path, texture_type, dds_format))
            
            if not textures_to_process:
                if progress_callback:
                    progress_callback(f"All {len(texture_batch)} textures already exist - skipping batch")
                return results
            
            if progress_callback:
                progress_callback(f"Processing {len(textures_to_process)} new textures (skipped {len(texture_batch) - len(textures_to_process)} existing)...")
            
            # Group textures by format and output directory for optimal batching
            format_groups = {}
            for i, bl_image, output_path, texture_type, dds_format in textures_to_process:
                if dds_format is None:
                    dds_format = self.get_recommended_format(texture_type)
                
                output_dir = os.path.dirname(output_path)
                texconv_format = self._format_map.get(dds_format, 'BC7_UNORM_SRGB')
                
                group_key = (output_dir, texconv_format)
                if group_key not in format_groups:
                    format_groups[group_key] = []
                
                format_groups[group_key].append((i, bl_image, output_path, texture_type, dds_format))
            
            # Process each format group with a single texconv call
            for (output_dir, texconv_format), group_items in format_groups.items():
                if progress_callback:
                    progress_callback(f"Converting {len(group_items)} textures with format {texconv_format}...")
                
                # Create temporary input files for this group
                group_temp_files = []
                temp_to_final_mapping = {}
                
                for i, bl_image, output_path, texture_type, dds_format in group_items:
                    # Create temporary PNG file
                    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    temp_input_path = temp_file.name
                    temp_file.close()
                    
                    try:
                        # Save Blender image to temporary file
                        self._save_blender_image_to_file(bl_image, temp_input_path)
                        group_temp_files.append(temp_input_path)
                        temp_files.append(temp_input_path)
                        
                        # Map temp file to final output
                        temp_base = os.path.splitext(os.path.basename(temp_input_path))[0]
                        temp_output_path = os.path.join(output_dir, f"{temp_base}.dds")
                        temp_to_final_mapping[temp_output_path] = (i, output_path)
                        
                    except Exception as e:
                        if progress_callback:
                            progress_callback(f"Error saving {bl_image.name}: {e}")
                        continue
                
                if not group_temp_files:
                    continue
                
                # Ensure output directory exists
                os.makedirs(output_dir, exist_ok=True)
                
                # Run texconv on the entire batch
                cmd = [
                    self.texconv_path,
                    *group_temp_files,  # All input files
                    "-o", output_dir,
                    "-ft", "dds",
                    "-f", texconv_format,
                    "-m", "0",  # Generate all mipmaps
                    "-y",  # Overwrite existing
                    "-nologo",
                ]
                
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        check=False,
                        shell=False,
                        timeout=120  # 2 minute timeout for batch
                    )
                    
                    if result.returncode == 0:
                        # Check which files were successfully converted and rename them
                        for temp_output_path, (batch_index, final_output_path) in temp_to_final_mapping.items():
                            if os.path.exists(temp_output_path):
                                try:
                                    if temp_output_path != final_output_path:
                                        os.replace(temp_output_path, final_output_path)
                                    results[batch_index] = True
                                    if progress_callback:
                                        progress_callback(f"Converted: {os.path.basename(final_output_path)}")
                                except Exception as e:
                                    if progress_callback:
                                        progress_callback(f"Error moving {temp_output_path}: {e}")
                            else:
                                if progress_callback:
                                    progress_callback(f"Missing output: {temp_output_path}")
                    else:
                        if progress_callback:
                            progress_callback(f"texconv batch failed: {result.stderr}")
                
                except subprocess.TimeoutExpired:
                    if progress_callback:
                        progress_callback("texconv batch timed out")
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"Error running texconv batch: {e}")
        
        finally:
            # Cleanup temporary files
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except:
                    pass
        
        successful_count = sum(results)
        if progress_callback:
            progress_callback(f"Batch complete: {successful_count}/{len(texture_batch)} successful")
        
        return results
    
    # --- Queue Integration Methods ---
    
    async def queue_texture_conversion(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """Queue a texture conversion task and return task ID."""
        queue = get_texture_queue()
        return await queue.add_task(bl_image, output_path, texture_type, dds_format, progress_callback)
    
    async def queue_batch_conversion(
        self, 
        textures: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[str]:
        """Queue multiple texture conversion tasks and return list of task IDs.
        
        Args:
            textures: List of (bl_image, output_path, texture_type, dds_format) tuples
            progress_callback: Optional progress callback function
            
        Returns:
            List of task IDs for tracking
        """
        queue = get_texture_queue()
        return await queue.add_batch(textures, progress_callback)
    
    async def wait_for_conversions(
        self, 
        task_ids: List[str], 
        timeout: Optional[float] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Wait for multiple texture conversion tasks to complete."""
        queue = get_texture_queue()
        return await queue.wait_for_all(task_ids, timeout, progress_callback)
    
    def get_conversion_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a texture conversion task."""
        queue = get_texture_queue()
        return queue.get_task_status(task_id)
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get overall texture processing queue status."""
        queue = get_texture_queue()
        return queue.get_queue_status()
    
    async def process_textures_parallel(
        self,
        textures: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None
    ) -> Dict[str, bool]:
        """Process multiple textures in parallel using batching and return results.
        
        Args:
            textures: List of (bl_image, output_path, texture_type, dds_format) tuples
            progress_callback: Optional progress callback function
            timeout: Optional timeout in seconds
            
        Returns:
            Dictionary mapping task_id to success status
        """
        if not textures:
            return {}
        
        start_time = asyncio.get_event_loop().time()
        
        if progress_callback:
            progress_callback(f"Starting parallel processing of {len(textures)} textures with batching...")
            progress_callback(f"Using {self.max_concurrent_processes} concurrent texconv processes")
            progress_callback(f"Batch size: {self.batch_size} textures per process")
        
        # Queue all tasks - they will be automatically batched
        task_ids = await self.queue_batch_conversion(textures, progress_callback)
        
        # Wait for completion
        success = await self.wait_for_conversions(task_ids, timeout, progress_callback)
        
        # Collect results
        results = {}
        queue = get_texture_queue()
        successful_count = 0
        
        for task_id in task_ids:
            task_status = queue.get_task_status(task_id)
            if task_status:
                task_success = task_status.get('status') == 'completed'
                results[task_id] = task_success
                if task_success:
                    successful_count += 1
            else:
                results[task_id] = False
        
        # Performance metrics
        end_time = asyncio.get_event_loop().time()
        total_time = end_time - start_time
        
        if progress_callback:
            progress_callback(f"Parallel processing completed in {total_time:.2f} seconds")
            progress_callback(f"Success rate: {successful_count}/{len(textures)} ({successful_count/len(textures)*100:.1f}%)")
            if total_time > 0:
                throughput = len(textures) / total_time
                progress_callback(f"Throughput: {throughput:.2f} textures/second")
        
        return results
    
    # --- Direct Conversion Methods ---
    
    async def convert_png_to_dds_async(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a Blender image to DDS format asynchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        # Use recommended format if not specified
        if dds_format is None:
            dds_format = self.get_recommended_format(texture_type)
        
        def _convert():
            return self._convert_png_to_dds_sync(bl_image, output_path, dds_format, progress_callback)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(get_thread_pool(), _convert)
    
    def convert_png_to_dds_sync(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a Blender image to DDS format synchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        # Use recommended format if not specified
        if dds_format is None:
            dds_format = self.get_recommended_format(texture_type)
        
        return self._convert_png_to_dds_sync(bl_image, output_path, dds_format, progress_callback)
    
    def _convert_png_to_dds_sync(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        dds_format: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Internal PNG to DDS conversion implementation."""
        try:
            if progress_callback:
                progress_callback(f"Converting {bl_image.name} to DDS...")
            
            # Create output directory
            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            # Create temporary PNG file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_input_path = temp_file.name
            
            try:
                # Save Blender image to temporary file
                self._save_blender_image_to_file(bl_image, temp_input_path)
                
                if progress_callback:
                    progress_callback("Running texconv...")
                
                # Convert using texconv
                texconv_format = self._format_map.get(dds_format, 'BC7_UNORM_SRGB')
                cmd = [
                    self.texconv_path,
                    temp_input_path,
                    "-o", output_dir,
                    "-ft", "dds",
                    "-f", texconv_format,
                    "-m", "0",  # Generate all mipmaps
                    "-y",  # Overwrite existing
                    "-nologo",
                ]
                
                result = subprocess.run(
                    cmd, 
                    capture_output=True, 
                    text=True, 
                    check=False, 
                    shell=False,
                    timeout=60  # 60 second timeout
                )
                
                if result.returncode != 0:
                    if progress_callback:
                        progress_callback(f"texconv failed: {result.stderr}")
                    return False
                
                # Handle texconv output file naming
                temp_output_base = os.path.splitext(os.path.basename(temp_input_path))[0]
                temp_dds_filename = f"{temp_output_base}.dds"
                temp_dds_path = os.path.join(output_dir, temp_dds_filename)
                
                if not os.path.exists(temp_dds_path):
                    if progress_callback:
                        progress_callback("texconv output file not found")
                    return False
                
                # Rename to final output path
                if temp_dds_path != output_path:
                    os.replace(temp_dds_path, output_path)
                
                if progress_callback:
                    progress_callback("Conversion complete")
                
                return True
                
            finally:
                # Cleanup temporary file
                if os.path.exists(temp_input_path):
                    try:
                        os.remove(temp_input_path)
                    except:
                        pass
                        
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
    
    def convert_dds_to_png_sync(
        self,
        dds_path: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert DDS file to PNG format synchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        try:
            if progress_callback:
                progress_callback(f"Converting {os.path.basename(dds_path)} to PNG...")
            
            # Create output directory
            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            # Convert DDS to PNG using texconv
            cmd = [
                self.texconv_path,
                dds_path,
                "-o", output_dir,
                "-ft", "png",
                "-y",  # Overwrite existing
                "-nologo",
            ]
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                check=False, 
                shell=False,
                timeout=30
            )
            
            if result.returncode != 0:
                if progress_callback:
                    progress_callback(f"texconv failed: {result.stderr}")
                return False
            
            # Handle texconv output file naming
            original_name = os.path.splitext(os.path.basename(dds_path))[0]
            texconv_output = os.path.join(output_dir, f"{original_name}.png")
            
            if os.path.exists(texconv_output):
                # Rename to final output path if different
                if texconv_output != output_path:
                    os.replace(texconv_output, output_path)
                
                if progress_callback:
                    progress_callback("Conversion complete")
                return True
            else:
                if progress_callback:
                    progress_callback("texconv output file not found")
                return False
                
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
    
    async def convert_dds_to_png_async(
        self,
        dds_path: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert DDS file to PNG format asynchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        def _convert():
            return self.convert_dds_to_png_sync(dds_path, output_path, progress_callback)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(get_thread_pool(), _convert)
    
    def batch_convert_dds_to_png(
        self,
        dds_files: List[str],
        output_dir: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> List[str]:
        """Batch convert multiple DDS files to PNG."""
        if not self.is_available():
            return []
        
        converted_files = []
        total_files = len(dds_files)
        
        for i, dds_file in enumerate(dds_files):
            if progress_callback:
                progress_callback(i, total_files, f"Converting {os.path.basename(dds_file)}")
            
            base_name = os.path.splitext(os.path.basename(dds_file))[0]
            output_path = os.path.join(output_dir, f"{base_name}.png")
            
            if self.convert_dds_to_png_sync(dds_file, output_path):
                converted_files.append(output_path)
        
        if progress_callback:
            progress_callback(total_files, total_files, "Batch conversion complete")
        
        return converted_files
    
    def _save_blender_image_to_file(self, bl_image: bpy.types.Image, filepath: str):
        """Save a Blender image to a file."""
        scene = bpy.context.scene
        render_settings = scene.render
        
        # Store original settings
        orig_filepath = render_settings.filepath
        orig_format = render_settings.image_settings.file_format
        orig_color_mode = render_settings.image_settings.color_mode
        orig_color_depth = render_settings.image_settings.color_depth
        
        try:
            # Set temporary render settings
            render_settings.filepath = filepath
            render_settings.image_settings.file_format = 'PNG'
            render_settings.image_settings.color_mode = 'RGBA'
            render_settings.image_settings.color_depth = '8'
            
            # Save the image
            bl_image.save_render(filepath=filepath, scene=scene)
            
        finally:
            # Restore original settings
            render_settings.filepath = orig_filepath
            render_settings.image_settings.file_format = orig_format
            render_settings.image_settings.color_mode = orig_color_mode
            render_settings.image_settings.color_depth = orig_color_depth

    # Legacy method names for backward compatibility
    async def convert_texture_async(self, bl_image, output_path, dds_format='BC7_UNORM_SRGB', progress_callback=None):
        """Legacy method - use convert_png_to_dds_async instead."""
        return await self.convert_png_to_dds_async(bl_image, output_path, 'base color', dds_format, progress_callback)

# Global texture processor instance
_texture_processor = None

def get_texture_processor() -> TextureProcessor:
    """Get the global texture processor."""
    global _texture_processor
    if _texture_processor is None:
        _texture_processor = TextureProcessor()
    return _texture_processor

# --- Progress Tracking ---

class ProgressTracker:
    """Tracks progress for long-running operations."""
    
    def __init__(self, total_steps: int, callback: Optional[Callable[[float, str], None]] = None):
        self.total_steps = total_steps
        self.current_step = 0
        self.callback = callback
        self._cancelled = False
    
    def update(self, message: str = ""):
        """Update progress."""
        if self._cancelled:
            return False
        
        progress = self.current_step / self.total_steps if self.total_steps > 0 else 0.0
        if self.callback:
            self.callback(progress, message)
        return True
    
    def step(self, message: str = ""):
        """Advance to next step."""
        self.current_step += 1
        return self.update(message)
    
    def cancel(self):
        """Cancel the operation."""
        self._cancelled = True
    
    def is_cancelled(self) -> bool:
        """Check if operation was cancelled."""
        return self._cancelled

# --- Cleanup Functions ---

def cleanup_addon_resources():
    """Cleanup all addon resources."""
    cleanup_thread_pool()
    get_stage_manager().cleanup_all()

# Global background processing state
_background_processor = None

class BackgroundTextureProcessor:
    """Handles texture processing in the background without blocking Blender's main thread."""
    
    def __init__(self):
        self.active_jobs = {}  # job_id -> job_info
        self.completed_jobs = {}  # job_id -> results
        self.job_counter = 0
        self.timer_registered = False
        
    def start_background_job(self, textures: List[Tuple], progress_callback=None, completion_callback=None):
        """Start a background texture processing job."""
        self.job_counter += 1
        job_id = f"texture_job_{self.job_counter}"
        
        job_info = {
            'id': job_id,
            'textures': textures,
            'progress_callback': progress_callback,
            'completion_callback': completion_callback,
            'status': 'queued',
            'progress': 0,
            'total': len(textures),
            'results': [],
            'thread': None,
            'start_time': time.time()
        }
        
        self.active_jobs[job_id] = job_info
        
        # Start the processing thread
        import threading
        thread = threading.Thread(target=self._process_job_thread, args=(job_id,))
        thread.daemon = True
        job_info['thread'] = thread
        thread.start()
        
        # Register timer if not already registered
        if not self.timer_registered:
            bpy.app.timers.register(self._timer_callback, first_interval=0.1, persistent=True)
            self.timer_registered = True
            
        print(f"Started background texture job: {job_id} with {len(textures)} textures")
        return job_id
    
    def _process_job_thread(self, job_id):
        """Process textures in a background thread."""
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return
            
        try:
            job_info['status'] = 'processing'
            texture_processor = get_texture_processor()
            
            if not texture_processor.is_available():
                job_info['status'] = 'failed'
                job_info['error'] = 'texconv.exe not available'
                return
            
            # Process textures in batches
            textures = job_info['textures']
            batch_size = 4  # Process 4 textures per batch
            results = []
            
            for i in range(0, len(textures), batch_size):
                batch = textures[i:i + batch_size]
                
                # Process this batch
                batch_results = texture_processor._convert_texture_batch_sync(
                    batch,
                    progress_callback=lambda msg: self._update_progress(job_id, msg)
                )
                
                results.extend(batch_results)
                job_info['progress'] = len(results)
                
                # Small delay to prevent overwhelming the system
                time.sleep(0.1)
                
                # Check if job was cancelled
                if job_info.get('cancelled', False):
                    job_info['status'] = 'cancelled'
                    return
            
            job_info['results'] = results
            job_info['status'] = 'completed'
            
        except Exception as e:
            job_info['status'] = 'failed'
            job_info['error'] = str(e)
            print(f"Background job {job_id} failed: {e}")
    
    def _update_progress(self, job_id, message):
        """Update progress from background thread."""
        job_info = self.active_jobs.get(job_id)
        if job_info and job_info.get('progress_callback'):
            # Store message for main thread to pick up
            job_info['last_message'] = message
    
    def _timer_callback(self):
        """Timer callback to check job status and update UI."""
        completed_jobs = []
        
        for job_id, job_info in self.active_jobs.items():
            # Update progress callback if available
            if 'last_message' in job_info and job_info.get('progress_callback'):
                try:
                    job_info['progress_callback'](job_info['last_message'])
                    del job_info['last_message']
                except:
                    pass  # Ignore callback errors
            
            # Check if job is completed
            if job_info['status'] in ['completed', 'failed', 'cancelled']:
                completed_jobs.append(job_id)
                
                # Call completion callback
                if job_info.get('completion_callback'):
                    try:
                        job_info['completion_callback'](job_id, job_info)
                    except Exception as e:
                        print(f"Error in completion callback for {job_id}: {e}")
        
        # Move completed jobs
        for job_id in completed_jobs:
            self.completed_jobs[job_id] = self.active_jobs.pop(job_id)
        
        # Continue timer if there are active jobs
        if self.active_jobs:
            return 0.1  # Check again in 0.1 seconds
        else:
            self.timer_registered = False
            return None  # Stop timer
    
    def get_job_status(self, job_id):
        """Get status of a background job."""
        if job_id in self.active_jobs:
            job_info = self.active_jobs[job_id]
            return {
                'status': job_info['status'],
                'progress': job_info['progress'],
                'total': job_info['total'],
                'elapsed': time.time() - job_info['start_time']
            }
        elif job_id in self.completed_jobs:
            job_info = self.completed_jobs[job_id]
            return {
                'status': job_info['status'],
                'progress': job_info['total'],
                'total': job_info['total'],
                'elapsed': time.time() - job_info['start_time'],
                'results': job_info.get('results', [])
            }
        else:
            return None
    
    def cancel_job(self, job_id):
        """Cancel a background job."""
        if job_id in self.active_jobs:
            self.active_jobs[job_id]['cancelled'] = True
            return True
        return False
    
    def cleanup_completed_jobs(self):
        """Clean up old completed jobs."""
        self.completed_jobs.clear()

def get_background_processor():
    """Get or create the global background processor."""
    global _background_processor
    if _background_processor is None:
        _background_processor = BackgroundTextureProcessor()
    return _background_processor 
"""
GREENIUM BLENDER SERVER
========================
Run this inside Blender's Scripting tab to connect Greenium to Blender.

SETUP:
  1. Open Blender
  2. Go to the Scripting tab
  3. Click New, paste this entire script
  4. Click Run Script (▶)
  5. A local server starts on port 8765
  6. In Greenium, select "Blender Local" mode and click Connect
  7. Keith's creations will appear live in Blender!

REQUIREMENTS:
  - Blender 3.x or 4.x
  - Greenium running on the same computer (localhost)
"""

import bpy
import json
import math
import threading
import base64
import io
import os
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8765

# ── CORS + request handler ───────────────────────────────────────────────────
class GreeniumHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silence default logging

    def send_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == '/ping':
            self.send_response(200)
            self.send_cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'version': '1.0', 'name': 'Greenium Blender Server'}).encode())

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception as e:
            self.send_response(400)
            self.send_cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        action = data.get('action', '')
        result = {'ok': True}

        try:
            if action == 'clear':
                bpy.app.timers.register(lambda: (clear_scene(), None)[1], first_interval=0.0)
                result = {'ok': True, 'msg': 'Scene cleared'}

            elif action == 'add_objects':
                objects = data.get('objects', [])
                code = data.get('code', '')
                # Schedule on main thread
                def do_add():
                    if code:
                        exec(code, {'bpy': bpy, 'math': math, 'random': __import__('random'), 'mat': make_material, 'link': link_mat})
                    elif objects:
                        for obj in objects:
                            add_object_from_desc(obj)
                    return None
                bpy.app.timers.register(do_add, first_interval=0.05)
                result = {'ok': True, 'msg': f'Adding {len(objects)} objects'}

            elif action == 'render':
                # Render and return base64 image
                img_data = render_scene(
                    width=data.get('width', 512),
                    height=data.get('height', 384),
                    samples=data.get('samples', 32)
                )
                result = {'ok': True, 'image': img_data}

            elif action == 'render_and_get':
                # Add objects then render
                objects = data.get('objects', [])
                code = data.get('code', '')
                import time
                # Add objects synchronously on main thread via timer
                added = [False]
                def do_add_and_wait():
                    if code:
                        try:
                            exec(code, {'bpy': bpy, 'math': math, 'random': __import__('random'), 'mat': make_material, 'link': link_mat})
                        except Exception as ex:
                            print(f'Code exec error: {ex}')
                    elif objects:
                        for obj in objects:
                            try:
                                add_object_from_desc(obj)
                            except Exception as ex:
                                print(f'Object add error: {ex}')
                    added[0] = True
                    return None
                bpy.app.timers.register(do_add_and_wait, first_interval=0.05)
                # Wait for main thread
                timeout = 10
                start = time.time()
                while not added[0] and time.time() - start < timeout:
                    time.sleep(0.1)
                img_data = render_scene(data.get('width', 512), data.get('height', 384), data.get('samples', 48))
                result = {'ok': True, 'image': img_data}

            elif action == 'exec_python':
                code = data.get('code', '')
                errors = []
                def run_code():
                    try:
                        exec(code, {'bpy': bpy, 'math': math, 'random': __import__('random'), 'mat': make_material, 'link': link_mat})
                    except Exception as e:
                        errors.append(str(e))
                    return None
                bpy.app.timers.register(run_code, first_interval=0.05)
                import time; time.sleep(0.3)
                result = {'ok': len(errors)==0, 'errors': errors}

            elif action == 'get_scene_info':
                obj_names = [o.name for o in bpy.context.scene.objects]
                result = {'ok': True, 'objects': obj_names, 'count': len(obj_names)}

        except Exception as e:
            result = {'ok': False, 'error': str(e)}

        self.send_response(200)
        self.send_cors()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


# ── BLENDER HELPERS ──────────────────────────────────────────────────────────
def make_material(name, color=(0.5, 0.5, 0.5), rough=0.5, metal=0.0, emit=None, emit_str=2.0, alpha=1.0):
    """Create a PBR material and return it."""
    m = bpy.data.materials.new(name=name)
    m.use_nodes = True
    nodes = m.node_tree.nodes
    links = m.node_tree.links
    nodes.clear()
    out = nodes.new('ShaderNodeOutputMaterial')
    pbr = nodes.new('ShaderNodeBsdfPrincipled')
    pbr.inputs['Base Color'].default_value = (*color, 1.0)
    pbr.inputs['Roughness'].default_value = rough
    pbr.inputs['Metallic'].default_value = metal
    if alpha < 1.0:
        pbr.inputs['Alpha'].default_value = alpha
        m.blend_method = 'BLEND'
    if emit:
        pbr.inputs['Emission'].default_value = (*emit, 1.0)
        pbr.inputs['Emission Strength'].default_value = emit_str
    links.new(pbr.outputs['BSDF'], out.inputs['Surface'])
    return m

def link_mat(obj, mat):
    """Attach material to object."""
    if obj.data and hasattr(obj.data, 'materials'):
        obj.data.materials.append(mat)
    return obj

def clear_scene():
    """Remove all mesh/light/camera objects except the default camera."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        bpy.data.meshes.remove(block)
    for block in list(bpy.data.materials):
        bpy.data.materials.remove(block)
    # Re-add camera and sun
    bpy.ops.object.camera_add(location=(0, -18, 8))
    cam = bpy.context.active_object
    cam.rotation_euler = (math.radians(65), 0, 0)
    bpy.context.scene.camera = cam
    bpy.ops.object.light_add(type='SUN', location=(5, -5, 10))
    sun = bpy.context.active_object
    sun.data.energy = 4.0
    sun.rotation_euler = (math.radians(60), 0, math.radians(30))
    # World sky
    world = bpy.context.scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs['Color'].default_value = (0.05, 0.08, 0.15, 1.0)
        bg.inputs['Strength'].default_value = 0.5
    print("[Greenium] Scene cleared and reset")

def add_object_from_desc(desc):
    """Add a 3D object from a JSON descriptor."""
    t = desc.get('type', 'sphere')
    x, y, z = desc.get('x', 0), desc.get('y', 0), desc.get('z', 0)
    color = tuple(desc.get('color', [0.5, 0.5, 0.5]))
    rough = desc.get('roughness', 0.6)
    metal = desc.get('metalness', 0.0)
    emit_raw = desc.get('emissive')
    emit = tuple(emit_raw) if emit_raw else None
    emit_str = desc.get('emissiveIntensity', 2.0)
    alpha = desc.get('alpha', 1.0)
    name = desc.get('name', t)

    if t == 'sphere':
        bpy.ops.mesh.primitive_uv_sphere_add(radius=desc.get('r', 1), location=(x, y, z), segments=32, ring_count=16)
        obj = bpy.context.active_object
        obj.modifiers.new('Sub', 'SUBSURF').levels = 2
    elif t == 'cube' or t == 'box':
        bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z))
        obj = bpy.context.active_object
        obj.scale = (desc.get('w', 1), desc.get('d', 1), desc.get('h', 1))
    elif t == 'cylinder':
        bpy.ops.mesh.primitive_cylinder_add(radius=desc.get('r', 1), depth=desc.get('h', 2), location=(x, y, z))
        obj = bpy.context.active_object
    elif t == 'cone':
        bpy.ops.mesh.primitive_cone_add(radius1=desc.get('r', 1), depth=desc.get('h', 2), location=(x, y, z))
        obj = bpy.context.active_object
    elif t == 'torus':
        bpy.ops.mesh.primitive_torus_add(major_radius=desc.get('r', 1.5), minor_radius=desc.get('tube', 0.4), location=(x, y, z))
        obj = bpy.context.active_object
    elif t == 'plane':
        bpy.ops.mesh.primitive_plane_add(size=desc.get('size', 2), location=(x, y, z))
        obj = bpy.context.active_object
    elif t == 'tree':
        obj = build_tree(x, y, z, desc)
    elif t == 'building':
        obj = build_building(x, y, z, desc)
    elif t == 'mountain':
        obj = build_mountain(x, y, z, desc)
    elif t == 'terrain':
        obj = build_terrain(x, y, z, desc)
    else:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(x, y, z))
        obj = bpy.context.active_object

    obj.name = name
    mat = make_material(f'mat_{name}', color, rough, metal, emit, emit_str, alpha)
    link_mat(obj, mat)

    # Apply rotation
    rx = desc.get('rx', 0)
    ry = desc.get('ry', 0)
    rz = desc.get('rz', 0)
    obj.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
    return obj

def build_tree(x, y, z, desc):
    h = desc.get('h', 3)
    r = desc.get('r', 0.3)
    # Trunk
    bpy.ops.mesh.primitive_cylinder_add(radius=r * 0.15, depth=h * 0.35, location=(x, y, z + h * 0.175))
    trunk = bpy.context.active_object
    trunk.name = 'trunk'
    mat_trunk = make_material('trunk_mat', (0.35, 0.18, 0.05), 0.9)
    link_mat(trunk, mat_trunk)
    # Canopy layers
    foliage_color = tuple(desc.get('foliage_color', [0.1, 0.5, 0.1]))
    for i in range(3):
        layer_r = r * (1.1 - i * 0.25)
        layer_z = z + h * (0.45 + i * 0.18)
        bpy.ops.mesh.primitive_cone_add(radius1=layer_r, depth=h * 0.38, location=(x, y, layer_z))
        cone = bpy.context.active_object
        cone.modifiers.new('Sub', 'SUBSURF').levels = 1
        fc = tuple(c * (0.8 + i * 0.1) for c in foliage_color)
        link_mat(cone, make_material(f'foliage_{i}', fc, 0.85))
    # Return trunk as parent (simplified)
    return trunk

def build_building(x, y, z, desc):
    w = desc.get('w', 2)
    d = desc.get('d', 2)
    h = desc.get('h', 4)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z + h / 2))
    obj = bpy.context.active_object
    obj.scale = (w, d, h)
    # Add bevel for nicer edges
    obj.modifiers.new('Bvl', 'BEVEL').width = 0.04
    return obj

def build_mountain(x, y, z, desc):
    h = desc.get('h', 4)
    r = desc.get('r', 2.5)
    bpy.ops.mesh.primitive_cone_add(radius1=r, depth=h, vertices=8, location=(x, y, z + h / 2))
    obj = bpy.context.active_object
    # Add displacement for rocky look
    sub = obj.modifiers.new('Sub', 'SUBSURF')
    sub.levels = 3
    disp = obj.modifiers.new('Dis', 'DISPLACE')
    tex = bpy.data.textures.new('MountainNoise', 'CLOUDS')
    tex.noise_scale = 0.8
    disp.texture = tex
    disp.strength = desc.get('roughness', 0.4)
    return obj

def build_terrain(x, y, z, desc):
    size = desc.get('size', 10)
    bpy.ops.mesh.primitive_plane_add(size=size, location=(x, y, z))
    obj = bpy.context.active_object
    sub = obj.modifiers.new('Sub', 'SUBSURF')
    sub.levels = 5
    sub.subdivision_type = 'SIMPLE'
    disp = obj.modifiers.new('Dis', 'DISPLACE')
    tex = bpy.data.textures.new('TerrainNoise', 'CLOUDS')
    tex.noise_scale = desc.get('noise_scale', 1.5)
    disp.texture = tex
    disp.strength = desc.get('height', 1.5)
    return obj

def render_scene(width=512, height=384, samples=32):
    """Render current scene and return as base64 PNG."""
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.image_settings.file_format = 'PNG'

    # Make sure there's a camera
    if not scene.camera:
        bpy.ops.object.camera_add(location=(0, -18, 8))
        cam = bpy.context.active_object
        cam.rotation_euler = (math.radians(65), 0, 0)
        scene.camera = cam

    # Render to temp file
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.close()
    scene.render.filepath = tmp.name
    bpy.ops.render.render(write_still=True)

    # Read and encode
    with open(tmp.name, 'rb') as f:
        data = f.read()
    os.unlink(tmp.name)
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


# ── SERVER MANAGEMENT ────────────────────────────────────────────────────────
_server = None
_server_thread = None

def start_server():
    global _server, _server_thread
    if _server is not None:
        print("[Greenium] Server already running on port", PORT)
        return
    _server = HTTPServer(('localhost', PORT), GreeniumHandler)
    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    print(f"[Greenium] ✅ Server started on http://localhost:{PORT}")
    print("[Greenium] Open Greenium in your browser and select 'Blender Local' mode.")
    print("[Greenium] Click 'Connect to Blender' to link them up.")

def stop_server():
    global _server, _server_thread
    if _server:
        _server.shutdown()
        _server = None
        _server_thread = None
        print("[Greenium] Server stopped.")

# ── INITIAL SCENE SETUP ──────────────────────────────────────────────────────
def setup_initial_scene():
    """Set up a clean scene ready for Greenium."""
    # Clear everything
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    # Camera
    bpy.ops.object.camera_add(location=(0, -18, 8))
    cam = bpy.context.active_object
    cam.rotation_euler = (math.radians(65), 0, 0)
    cam.name = 'GreeniumCamera'
    bpy.context.scene.camera = cam

    # Sun
    bpy.ops.object.light_add(type='SUN', location=(8, -5, 12))
    sun = bpy.context.active_object
    sun.data.energy = 3.5
    sun.data.color = (1.0, 0.95, 0.85)
    sun.rotation_euler = (math.radians(55), 0, math.radians(25))
    sun.name = 'GreeniumSun'

    # Area fill light
    bpy.ops.object.light_add(type='AREA', location=(-6, 4, 8))
    fill = bpy.context.active_object
    fill.data.energy = 60
    fill.data.color = (0.6, 0.75, 1.0)
    fill.data.size = 8
    fill.name = 'GreeniumFill'

    # World sky
    world = bpy.context.scene.world
    if not world:
        world = bpy.data.worlds.new('GreeniumWorld')
        bpy.context.scene.world = world
    world.use_nodes = True
    bg_node = world.node_tree.nodes.get('Background')
    if bg_node:
        bg_node.inputs['Color'].default_value = (0.04, 0.07, 0.14, 1.0)
        bg_node.inputs['Strength'].default_value = 0.45

    # Render settings
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.cycles.samples = 32
    bpy.context.scene.cycles.use_denoising = True
    bpy.context.scene.render.resolution_x = 512
    bpy.context.scene.render.resolution_y = 384

    print("[Greenium] Scene initialised and ready.")


# ── RUN ──────────────────────────────────────────────────────────────────────
setup_initial_scene()
start_server()

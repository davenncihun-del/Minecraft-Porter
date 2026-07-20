import json
import urllib.request
import uuid
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
sample_path = root / 'test_mod.zip'
output_path = root / 'test_mod-updated.zip'

with zipfile.ZipFile(sample_path, 'w') as archive:
    archive.writestr(
        'META-INF/neoforge.mods.toml',
        'modLoader="javafml"\n[[mods]]\nmodId="demo"\nversion="1.0.0"\n[[dependencies.demo]]\nmodId="minecraft"\nversionRange="[1.21.1]"\n'
    )
    archive.writestr(
        'src/main/java/demo/Compat.java',
        'package demo;\nimport net.minecraft.util.ResourceLocation;\nimport net.minecraftforge.common.MinecraftForge;\npublic class Compat {\n  public void init() { MinecraftForge.EVENT_BUS.register(this); }\n}\n'
    )
    archive.writestr(
        'fabric.mod.json',
        '{"schemaVersion": 1, "id": "demo", "version": "1.0.0", "depends": {"minecraft": "1.21.1"}}\n'
    )
    archive.writestr(
        'pack.mcmeta',
        '{"pack": {"pack_format": 15, "description": "demo"}}\n'
    )
    archive.writestr(
        'data/demo/recipes/example.json',
        '{"type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:stick"}], "result": {"item": "minecraft:stick"}}\n'
    )
    archive.writestr(
        'config/demo.properties',
        'minecraft_version=1.21.1\n'
    )

with sample_path.open('rb') as handle:
    data = handle.read()

boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
body = (
    b'--' + boundary.encode() + b'\r\n'
    b'Content-Disposition: form-data; name="file"; filename="test_mod.zip"\r\n'
    b'Content-Type: application/zip\r\n\r\n' + data + b'\r\n'
    b'--' + boundary.encode() + b'--\r\n'
)

request = urllib.request.Request('http://127.0.0.1:8000/upload', data=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
response = urllib.request.urlopen(request)
upload_info = json.loads(response.read().decode())
print('upload', upload_info)

update_request = urllib.request.Request(
    'http://127.0.0.1:8000/update',
    data=json.dumps({'file_id': upload_info['file_id'], 'loader': 'NeoForge', 'target_version': '1.21.5'}).encode(),
    headers={'Content-Type': 'application/json'}
)
update_response = urllib.request.urlopen(update_request)
update_response_data = json.loads(update_response.read().decode())
print('update response:', update_response_data)
download_url = update_response_data['download_url']
download_response = urllib.request.urlopen(f'http://127.0.0.1:8000{download_url}')
output_path.write_bytes(download_response.read())
print('updated', output_path.exists(), output_path.stat().st_size)

with zipfile.ZipFile(output_path) as archive:
    print('entries', sorted(archive.namelist()))

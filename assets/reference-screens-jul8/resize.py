from PIL import Image
import base64, json, os
specs = [
    ("library-grid.jpg", 1000, 66),
    ("loupe.jpg", 1000, 66),
    ("loupe-lights-out.jpg", 1000, 62),
    ("refine-mosaic.jpg", 1000, 64),
    ("mobile-library.jpg", 430, 72),
]
out = {}
for name, w, q in specs:
    im = Image.open(name).convert("RGB")
    ratio = w / im.width
    im2 = im.resize((w, round(im.height*ratio)), Image.LANCZOS)
    small = name.replace(".jpg", f".w{w}.jpg")
    im2.save(small, "JPEG", quality=q, optimize=True, progressive=True)
    b = os.path.getsize(small)
    with open(small, "rb") as f:
        uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    out[name] = {"uri": uri, "bytes": b, "b64len": len(uri), "dim": im2.size}
    print(f"{name:28} {im2.size}  {b//1024}KB  b64={len(uri)//1024}KB")
json.dump(out, open("datauris.json","w"))
print("total b64 chars:", sum(v["b64len"] for v in out.values()))

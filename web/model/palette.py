"""The naming palette: what a cluster may call itself.

Each entry is a name and the sentence the text tower reads for it. The name
is the product word — it appears on the proposal row, in the chip, and as
the collection's name when kept — and the prompt is tuned for the model,
which is why they are two columns and not one. The vocabulary of the
*library* stays bottom-up: nothing here labels a photograph; a term only
ever names a group that actually formed, and the owner renames, keeps or
ignores it. Editing this list is safe — assignments rewrite whole under a
stamp of the list itself, and a kept collection's chip keeps working by
matching (or, once gone, honestly matching nothing).
"""

PALETTE = [
    # places and light
    ("Landscapes", "a scenic landscape photograph"),
    ("Seascapes", "the sea or ocean coastline"),
    ("Cityscapes", "a city skyline or urban scene"),
    ("Nightscapes", "a city or landscape photographed at night"),
    ("Night sky", "the night sky full of stars"),
    ("Sunsets", "a sunset or sunrise with warm colored sky"),
    ("Mountains", "mountains or a mountain range"),
    ("Forests", "a forest or woodland"),
    ("Deserts", "a desert with sand or arid terrain"),
    ("Beaches", "a beach by the water"),
    ("Rivers and lakes", "a river, lake or pond"),
    ("Waterfalls", "a waterfall"),
    ("Snow and ice", "a snowy or icy winter scene"),
    ("Storms", "storm clouds, lightning or heavy weather"),
    ("Fog and mist", "a foggy or misty scene"),
    ("Fields", "an open field, meadow or farmland"),
    ("Canyons", "a canyon or rock formation"),
    ("Gardens", "a garden with plants and flowers"),
    # people
    ("Portraits", "a portrait photograph of a person"),
    ("Group photos", "a group of people posing together"),
    ("Candid people", "people photographed candidly, not posing"),
    ("Silhouettes", "a person in silhouette against the light"),
    ("Children", "a child or children"),
    ("Weddings", "a wedding"),
    ("Concerts", "a concert or live music performance"),
    ("Sports", "people playing a sport"),
    # built world
    ("Street photography", "a candid street photography scene"),
    ("Architecture", "a building photographed for its architecture"),
    ("Interiors", "the interior of a room or building"),
    ("Abandoned places", "an abandoned building or ruin"),
    ("Bridges", "a bridge"),
    ("Churches", "a church, cathedral or temple"),
    ("Construction", "a construction site with equipment"),
    ("Factories", "an industrial factory or plant"),
    ("Markets", "a market or shops with goods for sale"),
    ("Museums", "the inside of a museum or gallery"),
    ("Airports", "an airport with aircraft or terminals"),
    ("Harbors", "a harbor or marina with boats"),
    # animals
    ("Dogs", "a dog"),
    ("Cats", "a cat"),
    ("Birds", "a bird"),
    ("Horses", "a horse"),
    ("Wildlife", "a wild animal in nature"),
    ("Insects", "an insect or spider up close"),
    # machines that move
    ("Cars", "a car"),
    ("Motorcycles", "a motorcycle"),
    ("Bicycles", "a bicycle"),
    ("Trains", "a train or railway"),
    ("Aircraft", "an airplane or helicopter"),
    ("Boats", "a boat or ship"),
    ("Rockets", "a rocket, launch pad or spacecraft"),
    # things and craft
    ("Food", "a plate of food or a meal"),
    ("Drinks and coffee", "a drink, coffee or cocktail"),
    ("Flowers", "flowers in bloom"),
    ("Plants", "a plant or foliage up close"),
    ("Macro", "an extreme close-up macro photograph"),
    ("Still life", "a still life arrangement of objects"),
    ("Product shots", "a product photographed on a clean background"),
    ("Fireworks", "fireworks in the sky"),
    ("Workshop and tools", "a workshop with tools or machining equipment"),
    ("3D printing", "a 3D printer or 3D printed part"),
    ("Electronics", "electronic circuit boards or components"),
    ("Computers", "a computer, keyboard or screen setup"),
    ("Books and paper", "books, paper or handwriting"),
    ("Music gear", "a musical instrument or audio equipment"),
    ("Art and murals", "artwork, a mural or graffiti"),
    ("Signs", "a sign or typography"),
    ("Fashion", "a fashion photograph of clothing"),
    # records rather than photographs
    ("Screenshots", "a screenshot of a computer or phone screen"),
    ("Documents", "a photographed document, receipt or whiteboard"),
]

# The stamp names the list itself: change a word and every assignment is a
# stale answer under a dead stamp, rewritten on the next pass.


def stamp(model_key: str) -> str:
    import hashlib

    said = "\n".join(f"{name}\t{prompt}" for name, prompt in PALETTE)
    return f"{model_key}#{hashlib.sha1(said.encode('utf-8')).hexdigest()[:12]}"

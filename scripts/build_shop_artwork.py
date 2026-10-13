"""Build matching SVG and PNG shop fallbacks from the same vector primitives."""
from pathlib import Path
from html import escape
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "web" / "assets" / "shop"
INK, SAGE, PINK, CREAM = "#688271", "#A9C5A1", "#D995AA", "#FFF5E8"


class Drawing:
    def __init__(self):
        self.parts = []
        self.image = Image.new("RGBA", (768, 768))
        self.draw = ImageDraw.Draw(self.image)

    def shape(self, kind, coords, fill, stroke=INK, width=2):
        scaled = [v * 3 for v in coords]
        if kind == "ellipse":
            x, y, r, s = coords
            self.parts.append(f'<ellipse cx="{(x+r)/2}" cy="{(y+s)/2}" rx="{(r-x)/2}" ry="{(s-y)/2}"')
            self.draw.ellipse(scaled, fill=fill, outline=stroke, width=width*3)
        elif kind == "rect":
            x, y, r, s = coords
            self.parts.append(f'<rect x="{x}" y="{y}" width="{r-x}" height="{s-y}" rx="8"')
            self.draw.rounded_rectangle(scaled, radius=24, fill=fill, outline=stroke, width=width*3)
        else:
            points = list(zip(coords[::2], coords[1::2]))
            self.parts.append('<polygon points="' + ' '.join(f'{x},{y}' for x,y in points) + '"')
            self.draw.polygon(list(zip(scaled[::2], scaled[1::2])), fill=fill)
            if stroke:
                self.draw.line([*(tuple(p) for p in zip(scaled[::2],scaled[1::2])),(scaled[0],scaled[1])], fill=stroke, width=width*3, joint="curve")
        self.parts[-1] += f' fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width}" stroke-linejoin="round"/>'

    def line(self, coords, color=INK, width=2):
        self.parts.append('<polyline points="'+' '.join(f'{x},{y}' for x,y in zip(coords[::2],coords[1::2]))+f'" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')
        self.draw.line([(x*3,y*3) for x,y in zip(coords[::2],coords[1::2])],fill=color,width=width*3,joint="curve")

    def save(self, name):
        (ROOT/f"{name}.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img"><title>'+escape(name)+'</title>'+''.join(self.parts)+'</svg>',encoding="utf-8")
        self.image.resize((512,512),Image.Resampling.LANCZOS).save(ROOT/f"{name}.png")


def build():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("dessert", "drink", "meal", "gift"):
        d=Drawing()
        d.shape("ellipse",[29,192,227,225],"#EDF3EC",None)
        if name=="dessert":
            d.shape("poly",[57,98,191,110,191,196,57,183],CREAM)
            d.shape("poly",[57,145,191,157,191,174,57,162],PINK,None)
            d.shape("poly",[57,175,191,186,191,196,57,183],"#EACCA7",None)
            d.shape("poly",[57,98,145,64,191,110,100,137],"#FFFDF8")
            d.line([100,137,100,186],"#D9BEA7")
            d.shape("ellipse",[111,51,150,101],PINK)
            d.shape("poly",[126,59,111,47,128,49,136,38,138,52,153,48,142,63],SAGE)
            for x,y in [(123,72),(135,82),(125,89),(140,68)]:
                d.line([x,y,x+1,y+3],"#FFF9ED",2)
        elif name=="drink":
            d.shape("ellipse",[169,110,219,170],CREAM)
            d.shape("ellipse",[181,122,206,156],"#FFFFFF")
            d.shape("rect",[57,101,184,196],CREAM)
            d.shape("ellipse",[57,89,184,124],"#FAFEF8")
            d.shape("ellipse",[69,99,173,116],"#B5C99C",None)
            d.shape("poly",[111,147,129,126,136,149,125,169],SAGE,None)
            d.line([121,168,130,140])
            for x in (96,127,155):
                d.line([x,74,x-5,62,x+4,46],"#C1D4CA",3)
        elif name=="meal":
            d.shape("poly",[39,113,217,113,199,177,167,203,89,203,57,177],CREAM)
            d.shape("ellipse",[39,85,217,139],"#E7C49D")
            for y in (99,108,117):
                d.line([66,y,91,y-5,116,y+5,140,y-2,169,y+4,191,y],"#FFF3C6",4)
            d.shape("poly",[136,105,169,52,184,56,159,113],"#F0CD98")
            d.shape("poly",[169,52,171,36,181,48,194,38,184,56],PINK)
            d.shape("ellipse",[81,109,102,126],SAGE)
            d.shape("ellipse",[142,122,158,134],SAGE)
            d.line([74,171,183,171],"#ADC3B4",3)
            d.shape("ellipse",[121,169,136,184],PINK,None)
        else:
            d.shape("rect",[55,105,202,206],CREAM)
            d.shape("rect",[48,88,209,116],"#EAF1E7")
            d.shape("rect",[118,89,143,206],PINK,None)
            d.shape("ellipse",[83,50,132,86],"#EAC4CF")
            d.shape("ellipse",[130,50,179,86],"#EAC4CF")
            d.shape("ellipse",[120,72,143,93],PINK)
            d.line([122,86,104,132],PINK,5)
            d.line([140,86,157,130],PINK,5)
        d.save(name)


if __name__ == "__main__":
    build()

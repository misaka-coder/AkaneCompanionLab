//! Screenshot-pixel crop/zoom with an explicit inverse map to window pixels.
use serde_json::Value;
use super::capture::Frame;
#[derive(Clone,Copy)]
pub struct Region {pub x:u32,pub y:u32,pub width:u32,pub height:u32,pub scale:u32}
impl Region {
    pub fn from_args(args:&Value,width:u32,height:u32)->Result<Self,&'static str>{
        let crop=&args["region"];
        let region=if crop.is_object(){Self{x:crop["x"].as_u64().unwrap_or(u64::MAX) as u32,y:crop["y"].as_u64().unwrap_or(u64::MAX) as u32,
            width:crop["width"].as_u64().unwrap_or(0) as u32,height:crop["height"].as_u64().unwrap_or(0) as u32,scale:args["scale"].as_u64().unwrap_or(1) as u32}}
            else{Self{x:0,y:0,width,height,scale:args["scale"].as_u64().unwrap_or(1) as u32}};
        if region.width==0||region.height==0||!(1..=3).contains(&region.scale)||region.x as u64+region.width as u64>width as u64
            ||region.y as u64+region.height as u64>height as u64||region.width as u64*region.height as u64*(region.scale as u64).pow(2)>16_777_216{return Err("screenshot_region_invalid");}
        Ok(region)
    }
    pub fn point(&self,x:u64,y:u64)->Option<(u32,u32)>{
        if x>=self.width as u64*self.scale as u64||y>=self.height as u64*self.scale as u64{return None;}
        Some((self.x+x as u32/self.scale,self.y+y as u32/self.scale))
    }
    pub fn png(&self,frame:&Frame)->Result<Vec<u8>,&'static str>{
        if self.x==0&&self.y==0&&self.width==frame.width&&self.height==frame.height&&self.scale==1{return Ok(frame.png.clone());}
        let width=self.width*self.scale;let height=self.height*self.scale;let mut pixels=Vec::with_capacity(width as usize*height as usize*4);
        for y in 0..height {for x in 0..width {let (sx,sy)=self.point(x as u64,y as u64).unwrap();let i=((sy*frame.width+sx)*4) as usize;pixels.extend_from_slice(&frame.rgba[i..i+4]);}}
        let mut bytes=Vec::new();{
            let mut encoder=png::Encoder::new(&mut bytes,width,height);encoder.set_color(png::ColorType::Rgba);encoder.set_depth(png::BitDepth::Eight);
            encoder.write_header().map_err(|_|"capture_encode_failed")?.write_image_data(&pixels).map_err(|_|"capture_encode_failed")?;
        }
        if bytes.len()>8*1024*1024{return Err("capture_payload_too_large");}Ok(bytes)
    }
}
#[cfg(test)]mod tests{
    use super::*;use serde_json::json;
    #[test]fn scaled_crop_maps_back_without_crossing_edges(){
        let r=Region::from_args(&json!({"region":{"x":40,"y":20,"width":100,"height":50},"scale":3}),400,300).unwrap();
        assert_eq!(r.point(0,0),Some((40,20)));assert_eq!(r.point(299,149),Some((139,69)));assert_eq!(r.point(300,1),None);
        assert!(Region::from_args(&json!({"region":{"x":399,"y":0,"width":2,"height":3}}),400,300).is_err());
    }
}

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct HitRegion {
    #[serde(default, rename = "kind")]
    pub(crate) _kind: String,
    pub(crate) rect: HitRect,
    #[serde(default)]
    pub(crate) polygon: Vec<HitPoint>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct HitRect {
    pub(crate) x: i32,
    pub(crate) y: i32,
    pub(crate) width: i32,
    pub(crate) height: i32,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct HitPoint {
    pub(crate) x: i32,
    pub(crate) y: i32,
}

impl HitRegion {
    pub(crate) fn contains_point(&self, x: i32, y: i32) -> bool {
        if self.rect.width <= 0 || self.rect.height <= 0 {
            return false;
        }

        let right = self.rect.x.saturating_add(self.rect.width);
        let bottom = self.rect.y.saturating_add(self.rect.height);
        if x < self.rect.x || x > right || y < self.rect.y || y > bottom {
            return false;
        }

        self.polygon.len() < 3 || point_in_polygon(&self.polygon, x, y)
    }
}

pub(crate) fn point_in_polygon(points: &[HitPoint], x: i32, y: i32) -> bool {
    if points.len() < 3 {
        return false;
    }

    let px = x as f64;
    let py = y as f64;
    let mut inside = false;
    let mut previous = points.len() - 1;

    for current in 0..points.len() {
        let a = &points[current];
        let b = &points[previous];
        let ax = a.x as f64;
        let ay = a.y as f64;
        let bx = b.x as f64;
        let by = b.y as f64;

        if (ay > py) != (by > py) && px < (bx - ax) * (py - ay) / (by - ay) + ax {
            inside = !inside;
        }

        previous = current;
    }

    inside
}

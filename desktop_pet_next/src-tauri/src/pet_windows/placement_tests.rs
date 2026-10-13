use super::{models::WindowGeometry, placement::panel_position};

#[test]
fn secondary_and_negative_monitor_origins_are_respected() {
    let area = WindowGeometry {
        x: 1920,
        y: 120,
        width: 1920,
        height: 1040,
    };
    let pet = WindowGeometry {
        x: 2200,
        y: 400,
        width: 340,
        height: 560,
    };
    assert_eq!(panel_position(&pet, (400, 580), &area), (2546, 400));
    let area = WindowGeometry {
        x: -1920,
        y: -100,
        width: 1920,
        height: 1040,
    };
    let pet = WindowGeometry {
        x: -400,
        y: 500,
        width: 340,
        height: 560,
    };
    assert_eq!(panel_position(&pet, (400, 580), &area), (-806, 360));
}

#[test]
fn offscreen_or_oversized_panels_stay_at_the_work_area_edge() {
    let area = WindowGeometry {
        x: 50,
        y: 100,
        width: 300,
        height: 400,
    };
    let pet = WindowGeometry {
        x: -1000,
        y: -1000,
        width: 340,
        height: 560,
    };
    assert_eq!(panel_position(&pet, (400, 580), &area), (50, 100));
}

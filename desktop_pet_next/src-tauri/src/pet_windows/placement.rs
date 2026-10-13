use super::models::WindowGeometry;

pub(super) fn panel_position(
    pet: &WindowGeometry,
    panel_size: (u32, u32),
    work_area: &WindowGeometry,
) -> (i32, i32) {
    let size = |value: u32| i32::try_from(value).unwrap_or(i32::MAX);
    let panel_width = size(panel_size.0);
    let panel_height = size(panel_size.1);
    let last_x = work_area
        .x
        .saturating_add(size(work_area.width))
        .saturating_sub(panel_width)
        .max(work_area.x);
    let last_y = work_area
        .y
        .saturating_add(size(work_area.height))
        .saturating_sub(panel_height)
        .max(work_area.y);
    let right = pet.x.saturating_add(size(pet.width)).saturating_add(6);
    let x = if right >= work_area.x && right <= last_x {
        right
    } else {
        pet.x
            .saturating_sub(panel_width)
            .saturating_sub(6)
            .clamp(work_area.x, last_x)
    };
    let y = pet.y.clamp(work_area.y, last_y);
    (x, y)
}

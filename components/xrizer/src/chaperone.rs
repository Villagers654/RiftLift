use crate::openxr_data::RealOpenXrData;
use openvr as vr;
use std::sync::{
    Arc, OnceLock,
    atomic::{AtomicBool, Ordering},
};

fn bounds_visible_state() -> &'static AtomicBool {
    static BOUNDS_VISIBLE: OnceLock<AtomicBool> = OnceLock::new();
    BOUNDS_VISIBLE.get_or_init(|| AtomicBool::new(false))
}

fn play_area_quad(width: f32, height: f32) -> vr::HmdQuad_t {
    let half_x = width * 0.5;
    let half_z = height * 0.5;
    vr::HmdQuad_t {
        vCorners: [
            vr::HmdVector3_t {
                v: [-half_x, 0.0, -half_z],
            },
            vr::HmdVector3_t {
                v: [half_x, 0.0, -half_z],
            },
            vr::HmdVector3_t {
                v: [half_x, 0.0, half_z],
            },
            vr::HmdVector3_t {
                v: [-half_x, 0.0, half_z],
            },
        ],
    }
}

#[derive(macros::InterfaceImpl)]
#[interface = "IVRChaperone"]
#[versions(004, 003)]
pub struct Chaperone {
    vtables: Vtables,
    openxr: Arc<RealOpenXrData>,
    play_area: OnceLock<Option<(f32, f32)>>,
}

impl Chaperone {
    pub fn new(openxr: Arc<RealOpenXrData>) -> Self {
        Self {
            vtables: Default::default(),
            openxr,
            play_area: OnceLock::new(),
        }
    }

    fn play_area(&self) -> Option<(f32, f32)> {
        *self.play_area.get_or_init(|| {
            self.openxr
                .play_area_bounds()
                .map(|bounds| (bounds.width, bounds.height))
        })
    }
}

impl vr::IVRChaperone004_Interface for Chaperone {
    fn ResetZeroPose(&self, origin: vr::ETrackingUniverseOrigin) {
        self.openxr.reset_tracking_space(origin);
    }

    fn ForceBoundsVisible(&self, visible: bool) {
        bounds_visible_state().store(visible, Ordering::SeqCst);
    }
    fn AreBoundsVisible(&self) -> bool {
        bounds_visible_state().load(Ordering::SeqCst)
    }
    fn GetBoundsColor(
        &self,
        color_array: *mut vr::HmdColor_t,
        count: std::ffi::c_int,
        _collision_bounds_fade_distance: f32,
        camera_color: *mut vr::HmdColor_t,
    ) {
        crate::warn_unimplemented!("GetBoundsColor");
        if color_array.is_null() || camera_color.is_null() || count <= 0 {
            return;
        }
        let color_array = unsafe { std::slice::from_raw_parts_mut(color_array, count as usize) };
        color_array.fill(vr::HmdColor_t::default());
        unsafe {
            camera_color.write(vr::HmdColor_t::default());
        }
    }
    fn SetSceneColor(&self, _: vr::HmdColor_t) {
        crate::warn_unimplemented!("SetSceneColor");
    }
    fn ReloadInfo(&self) {
        crate::warn_unimplemented!("ReloadInfo");
    }
    fn GetPlayAreaRect(&self, rect: *mut vr::HmdQuad_t) -> bool {
        let Some((width, height)) = self.play_area() else {
            return false;
        };
        if rect.is_null() {
            return false;
        }
        unsafe { rect.write(play_area_quad(width, height)) };
        true
    }
    fn GetPlayAreaSize(&self, size_x: *mut f32, size_z: *mut f32) -> bool {
        let Some((width, height)) = self.play_area() else {
            return false;
        };
        if size_x.is_null() || size_z.is_null() {
            return false;
        }
        unsafe {
            size_x.write(width);
            size_z.write(height);
        }
        true
    }
    fn GetCalibrationState(&self) -> vr::ChaperoneCalibrationState {
        vr::ChaperoneCalibrationState::OK
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn play_area_is_centered_on_the_stage_origin() {
        let quad = play_area_quad(2.0, 4.0);
        assert_eq!(quad.vCorners[0].v, [-1.0, 0.0, -2.0]);
        assert_eq!(quad.vCorners[2].v, [1.0, 0.0, 2.0]);
    }
}

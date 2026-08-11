use libloading::{Library, Symbol};
use std::ffi::{c_char, c_void};

#[test]
#[cfg_attr(miri, ignore)]
fn smoke_test() {
    let path = test_cdylib::build_current_project();
    let lib = unsafe { Library::new(path) }.unwrap();
    let factory: Symbol<fn(*const c_char, *mut i32) -> *mut c_void> =
        unsafe { lib.get(b"VRClientCoreFactory\0") }.unwrap();

    let i = factory(c"IVRClientCore_003".as_ptr(), std::ptr::null_mut());
    assert!(!i.is_null());
}

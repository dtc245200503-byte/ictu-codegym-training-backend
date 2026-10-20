from fastapi import HTTPException
from app.models.room import Room
from app.models.attendance import StudentClass
from app.crud.student_enrollments import attendance_membership


def name_key(value):
    return " ".join((value or "").split()).casefold()


def room_item(room):
    return {key: getattr(room, key) for key in ["room_id", "name", "kind", "capacity", "meeting_url", "is_active", "version"]}


def room_options(db):
    return [room_item(row) for row in db.query(Room).filter_by(is_active=True).order_by(Room.name, Room.room_id)]


def resolve_room(db, room_id, room_name=None, retained_id=None):
    query = db.query(Room).populate_existing().with_for_update()
    room = query.filter_by(room_id=room_id).first() if room_id is not None else (
        query.filter_by(name_key=name_key(room_name)).first() if room_name else None)
    if room_id is not None and room is None:
        raise HTTPException(422, "Phòng học không tồn tại. Hãy tải lại danh mục phòng.")
    if room and not room.is_active and room.room_id != retained_id:
        raise HTTPException(422, "Phòng đã ngừng sử dụng. Hãy chọn phòng khác.")
    return room


def enrollment_size(db, class_id, day):
    managed, active, _ = attendance_membership(db, class_id, day)
    legacy = {id for (id,) in db.query(StudentClass.student_id).filter(StudentClass.class_id == class_id,
        (StudentClass.enrolled_date.is_(None)) | (StudentClass.enrolled_date <= day))}
    return len((legacy - managed) | active)


def room_info(db, room_id, fallback_name=None, class_id=None, day=None):
    room = db.get(Room, room_id) if room_id else None
    size = enrollment_size(db, class_id, day) if room and class_id and day else None
    warning = (f"Sĩ số {size} học viên vượt sức chứa {room.capacity} chỗ của phòng {room.name} ({size - room.capacity} chỗ thiếu)."
               if room and room.kind == "physical" and size is not None and size > room.capacity else None)
    return {"room_id": room.room_id if room else None, "room_name": room.name if room else fallback_name,
        "room_kind": room.kind if room else None, "room_capacity": room.capacity if room else None,
        "meeting_url": room.meeting_url if room else None, "room_active": room.is_active if room else None,
        "room_version": room.version if room else None, "class_size": size, "capacity_warning": warning}

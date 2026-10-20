from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.permissions import require_permission
from app.database import get_db
from app.models.room import Room
from app.models.training_program import TrainingClass
from app.schemas.room import RoomInput, RoomUpdate
from app.room_catalog import name_key, room_item, room_info
from app.routers.timetable import require_manager

router = APIRouter(prefix="/api/timetable/rooms", tags=["Phòng học"], dependencies=[Depends(require_manager)])


@router.get("")
def rooms(db: Session = Depends(get_db)):
    return [room_item(row) for row in db.query(Room).order_by(Room.name, Room.room_id)]


def capacity_check(db, room_id, class_id, on_date):
    if not db.get(Room, room_id):
        raise HTTPException(404, "Không tìm thấy phòng học.")
    if not db.get(TrainingClass, class_id):
        raise HTTPException(404, "Không tìm thấy lớp học.")
    return room_info(db, room_id, class_id=class_id, day=on_date)


@router.get("/{room_id}/capacity")
def check_capacity(room_id: int, class_id: int = Query(gt=0), on_date: date = Query(), db: Session = Depends(get_db)):
    return capacity_check(db, room_id, class_id, on_date)


@router.post("", status_code=201, dependencies=[Depends(require_permission("TIMETABLE_EDIT"))])
def create_room(data: RoomInput, db: Session = Depends(get_db)):
    row = Room(**data.model_dump(), name_key=name_key(data.name))
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Tên phòng đã tồn tại, kể cả phòng đã ngừng sử dụng.")
    db.refresh(row)
    return room_item(row)


@router.put("/{room_id}", dependencies=[Depends(require_permission("TIMETABLE_EDIT"))])
def update_room(room_id: int, data: RoomUpdate, db: Session = Depends(get_db)):
    if not db.get(Room, room_id):
        raise HTTPException(404, "Không tìm thấy phòng học.")
    changes = {**data.model_dump(exclude={"expected_version"}), "name_key": name_key(data.name), "version": data.expected_version + 1}
    try:
        changed = db.query(Room).filter_by(room_id=room_id, version=data.expected_version).update(changes, synchronize_session=False)
        if changed != 1:
            raise HTTPException(409, "Phòng vừa được cập nhật. Hãy tải lại danh mục trước khi sửa.")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Tên phòng đã tồn tại, kể cả phòng đã ngừng sử dụng.")
    except Exception:
        db.rollback()
        raise
    return room_item(db.query(Room).filter_by(room_id=room_id).populate_existing().one())

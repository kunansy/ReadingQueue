from pydantic import BaseModel


class CustomBaseModel(BaseModel):
    class Config:
        extra = 'forbid'

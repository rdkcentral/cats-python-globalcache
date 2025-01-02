FROM python:3.12-alpine

COPY . /python-globalcache

RUN pip install -r /python-globalcache/requirements.txt

RUN pip install /python-globalcache

WORKDIR /python-globalcache

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9710"]

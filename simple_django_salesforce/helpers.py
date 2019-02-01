import json
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils.dateparse import parse_datetime, parse_date


def join_values(values):
    concat_character = settings.SALESFORCE_MULTICHOICE_FIELD_SEPARATOR
    final_value = concat_character.join(values)
    return final_value


def split_values(value):
    return value.split(settings.SALESFORCE_MULTICHOICE_FIELD_SEPARATOR)


# Getting nested attributes for objects
def get_deep_attr(obj, attrs):
    for attr in attrs.split("."):
        obj = getattr(obj, attr)

    return obj


def get_serialized_data(obj, field_name, field_type):
    data = getattr(obj, field_name)
    if data is None:
        return data

    if field_type is models.DecimalField:
        data = str(data)
    elif field_type is models.DateTimeField:
        data = data.__format__('%Y-%m-%dT%H:%M:%SZ')
    elif field_type is models.DateField:
        data = data.__format__('%Y-%m-%d')
    elif field_type is property:
        if type(data) is Decimal:
            data = str(data)
    return data


def get_deserialized_data(data, field_type):
    if data is None or data == 'None':
        return None
    if field_type is models.DateTimeField:
        data = parse_datetime(data)
    elif field_type is models.DateField:
        data = parse_date(data)
    elif field_type is models.DecimalField:
        data = Decimal(data)
    elif field_type is models.BooleanField:
        if isinstance(data, str):
            if data.lower() in ['true', '1']:
                data = True
            elif data.lower() in ['false', '0']:
                data = False
            else:
                data = None
    return data


def get_nested_object(obj, field_str):
    for obj_ref in field_str.split('.')[:-1]:
        if hasattr(obj, obj_ref):
            obj = getattr(obj, obj_ref)
        else:
            raise LookupError("Can't found `%s` attribute in %s" % (obj_ref, obj))
        if obj is None:
            break
    return obj


# Checking if nested attributes for objects exist
def has_deep_attr(obj, attrs):
    try:
        get_deep_attr(obj, attrs)
        return True
    except AttributeError:
        return False


def make_salesforce_fields_list(object, field_map, readonly_fields=()):
    salesforce_fields = {}

    for object_field, salesforce_field in field_map.items():
        if object_field in readonly_fields:
            continue

        if not has_deep_attr(object, object_field):
            continue

        salesforce_fields[salesforce_field] = get_deep_attr(object, object_field)

    return salesforce_fields


# Get unmapped Salesforce key by name
def get_salesforce_key(field_map, field_name):
    return field_map.get(field_name)


# Get mapped Salesforce key field by object field name
def get_object_to_salesforce_mapping_key(object, field_name):
    return object.fields_map.get(field_name)


# Get mapped object field by Salesforce key
def get_salesforce_to_object_mapping_key(obj, field_name):
    inverse_map = {v: k for k, v in obj.fields_map.items()}
    return inverse_map.get(field_name)

from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = '''Creates django model, input SF table name
        Usage: ./manage.py sf_model <SF_table_name>
    '''

    ignore_fields = []

    field_map = {
        'DecimalField': (
            '''    %s = models.DecimalField(_('%s'), max_digits=%s, decimal_places=%s, blank=True, null=True)\n''',
            ('name', 'label', 'precision', 'scale')),
        'BooleanField': (
        '''    %s = models.BooleanField(_('%s'), default=%s)\n''',
        ('name', 'label', 'defaultValue')),
        'NullBooleanField': (
        '''    %s = models.NullBooleanField(_('%s'), blank=True, null=True)\n''',
        ('name', 'label')),
        'CharField': (
        '''    %s = models.CharField(_('%s'), max_length=%s, blank=True)\n''',
        ('name', 'label', 'length')),
        'TextField': (
        '''    %s = models.TextField(_('%s'), max_length=500, blank=True)\n''',
        ('name', 'label')),
        'IntegerField': (
        '''    %s = models.IntegerField(_('%s'), max_length=%s, blank=True, null=True)\n''',
        ('name', 'label', 'precision')),
        'DateTimeField': (
            '''    %s = models.DateTimeField(_('%s'), auto_now=False, auto_now_add=False, blank=True, null=True)\n''',
            ('name', 'label')),
        'DateField': (
            '''    %s = models.DateField(_('%s'), auto_now=False, auto_now_add=False, blank=True, null=True)\n''',
            ('name', 'label')),
        'URLField': ('''    %s = models.URLField(_('%s'), max_length=%s)\n''',
                     ('name', 'label', 'length')),

    }

    def add_arguments(self, parser):
        parser.add_argument('sf_table_name', nargs='+', type=str)

    def handle(self, *args, **options):
        """"""
        sf_name = options['sf_table_name'][0]
        client = settings.SALESFORCE_CLIENT
        model_client = getattr(client, sf_name)

        sf_meta = model_client.describe()
        fields = sf_meta['fields']
        result = '''class %s(SalesforceModel):\n''' % self.get_choice_value_name(
            sf_meta['label'])
        fields_name_map = ''

        for field_data in fields:
            field_name = self.gen_field_name(field_data['name'])
            definition = self.get_field_define(field_data)
            result += definition
            fields_name_map += '''        '%s': '%s',\n''' % (
            field_name, field_data['name'])

        choice_def = self.extract_choice(fields)
        print(choice_def)
        print(result)
        print('''    fields_map = {\n%s    }''' % fields_name_map)

    def gen_field_name(self, value):
        if len(value) > 2:
            if not all([letter.isupper() for letter in
                        value]):  # not all upper case
                for x in reversed(range(len(value))):
                    if value[x].isalpha() and value[x].isupper() and x > 0 and \
                                    value[x - 1] != '_':
                        value = '%s_%s' % (value[:x], value[x:])

        return value.lower()

    def get_field_type(self, soap_type):
        if soap_type:
            return self.field_map[
                soap_type] if soap_type in self.field_map else (None, ())

    def get_django_filed(self, field_type, field_data):
        template, info_list = self.field_map[field_type]
        field_infos = [field_data.get(x, None) for x in info_list]
        field_infos[0] = self.gen_field_name(field_infos[0])
        return template % tuple(field_infos)

    def get_choice_value_name(self, string, lower=False, upper=False):
        string = string.replace(' ', '_').replace('/', '_').replace('\\',
                                                                    '_').replace(
            '.', '_').replace(
            '-', '_').replace('(', '_').replace(')', '')
        if string[0].isdigit():
            string = 'var_' + string

        if lower:
            return string.lower()
        if upper:
            return string.upper()
        return string

    def extract_choice(self, fields):
        result = ''
        for field_meta in fields:
            if field_meta['type'] == 'picklist' and field_meta[
                'picklistValues']:
                choices_def = ''
                for choice in field_meta['picklistValues']:
                    name = self.get_choice_value_name(choice['label'],
                                                      upper=True)
                    result += "%s = '%s'\n" % (name, choice['value'])
                    choices_def += '    (%s, %s),\n' % (name, name)

                choices_name = '%s_CHOICES' % self.get_choice_value_name(
                    field_meta['label'], upper=True)
                choices_def = """\n%s = (\n%s)\n\n""" % (
                choices_name, choices_def)

                result += choices_def
        return result

    def get_field_define(self, field_data, ignore_builtin=False):
        if ignore_builtin and field_data['name'] in self.ignore_fields:
            # todo ignore some
            return ''

        if field_data['type'] == 'id' and field_data['soapType'] == 'tns:ID':
            return '''    id = models.AutoField(primary_key=True)\n'''
        elif field_data['type'] == 'reference' and field_data[
            'soapType'] == 'tns:ID':
            return '''    %s = models.ForeignKey(%s, blank=True, null=True)\n''' % (
                self.gen_field_name(field_data['name']),
                field_data['referenceTo'][0])
        elif field_data['type'] == 'string':
            if field_data['length'] < 256:
                return self.get_django_filed('CharField', field_data)
            else:
                return self.get_django_filed('TextField', field_data)
        elif field_data['type'] == 'datetime':
            return self.get_django_filed('DateTimeField', field_data)
        elif field_data['type'] == 'date':
            return self.get_django_filed('DateField', field_data)
        elif field_data['type'] == 'percent':
            return self.get_django_filed('DecimalField', field_data)
        elif field_data['type'] == 'picklist':
            template = '''    %s = models.CharField(_('%s'), choices=%s_CHOICES, max_length=%s, blank=True, null=True)\n'''
            return template % (self.gen_field_name(field_data['name']),
                               field_data['label'],
                               self.get_choice_value_name(field_data['label'],
                                                          upper=True),
                               field_data['length'])
        elif field_data['type'] == 'double':
            if field_data['scale'] == 0:
                return self.get_django_filed('IntegerField', field_data)
            else:
                return self.get_django_filed('DecimalField', field_data)
        elif field_data['type'] == 'currency':
            return self.get_django_filed('DecimalField', field_data)
        elif field_data['type'] == 'textarea':
            return self.get_django_filed('TextField', field_data)
        elif field_data['type'] == 'url':
            return self.get_django_filed('URLField', field_data)
        elif field_data['type'] == 'boolean':
            if field_data['defaultValue'] in [True, False]:
                return self.get_django_filed('BooleanField', field_data)
            else:
                return self.get_django_filed('NullBooleanField', field_data)

        raise NotImplementedError(
            'Salesforce field type "%s" not covered yet.' % field_data['type'])

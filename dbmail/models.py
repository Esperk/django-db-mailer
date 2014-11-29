# -*- encoding: utf-8 -*-

import datetime
import uuid
import os

from django.utils.translation import ugettext_lazy as _
from django.utils.html import strip_tags
from django.core.cache import cache
from django.conf import settings
from django.db import models
from django import VERSION

from dbmail.defaults import (
    PRIORITY_STEPS, UPLOAD_TO, DEFAULT_CATEGORY, AUTH_USER_MODEL,
    DEFAULT_FROM_EMAIL, DEFAULT_PRIORITY)

from dbmail.utils import premailer_transform
from dbmail.utils import clean_cache_key
from dbmail.fields import HTMLField
from dbmail import initial_signals


def _upload_mail_file(instance, filename):
    if instance is not None:
        ext = filename.split('.')[-1]
        filename = "%s.%s" % (str(uuid.uuid4()), ext)
        return os.path.join(UPLOAD_TO, filename)


class MailCategory(models.Model):
    name = models.CharField(_('Category'), max_length=25, unique=True)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail category')
        verbose_name_plural = _('Mail categories')


class MailFromEmailCredential(models.Model):
    host = models.CharField(_('Host'), max_length=50)
    port = models.PositiveIntegerField(_('Port'))
    username = models.CharField(
        _('Username'), max_length=50, null=True, blank=True)
    password = models.CharField(
        _('Password'), max_length=50, null=True, blank=True)
    use_tls = models.BooleanField(_('Use TLS'), default=False)
    fail_silently = models.BooleanField(_('Fail silently'), default=False)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)

    def _clean_cache(self):
        for obj in MailFromEmail.objects.filter(credential=self):
            obj._clean_template_cache()

    def delete(self, using=None):
        self._clean_cache()
        super(MailFromEmailCredential, self).delete(using)

    def save(self, *args, **kwargs):
        self._clean_cache()
        super(MailFromEmailCredential, self).save(*args, **kwargs)

    def __unicode__(self):
        return '%s/%s' % (self.username, self.host)

    class Meta:
        verbose_name = _('Mail auth settings')
        verbose_name_plural = _('Mail auth settings')


class MailFromEmail(models.Model):
    name = models.CharField(_('Name'), max_length=100)
    email = models.EmailField(_('Email'), unique=True)
    credential = models.ForeignKey(
        MailFromEmailCredential, verbose_name=_('Auth credentials'),
        blank=True, null=True, default=None)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)

    @property
    def get_mail_from(self):
        return u'%s <%s>' % (self.name, self.email)

    def _clean_template_cache(self):
        MailTemplate.clean_cache(from_email=self)

    def get_auth(self):
        if self.credential:
            return dict(
                host=self.credential.host,
                port=self.credential.port,
                username=self.credential.username,
                password=self.credential.password,
                use_tls=self.credential.use_tls,
                fail_silently=self.credential.fail_silently
            )

    def delete(self, using=None):
        self._clean_template_cache()
        super(MailFromEmail, self).delete(using)

    def save(self, *args, **kwargs):
        self._clean_template_cache()
        return super(MailFromEmail, self).save(*args, **kwargs)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail from')
        verbose_name_plural = _('Mail from')


class MailBcc(models.Model):
    email = models.EmailField(_('Email'), unique=True)
    is_active = models.BooleanField(_('Is active'), default=True)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)

    def __clean_cache(self):
        MailTemplate.clean_cache(bcc_email=self)

    def save(self, *args, **kwargs):
        self.__clean_cache()
        return super(MailBcc, self).save(*args, **kwargs)

    def delete(self, using=None):
        self.__clean_cache()
        super(MailBcc, self).delete(using)

    def __unicode__(self):
        return self.email

    class Meta:
        verbose_name = _('Mail Bcc')
        verbose_name_plural = _('Mail Bcc')


class MailTemplate(models.Model):
    name = models.CharField(_('Template name'), max_length=100, db_index=True)
    subject = models.CharField(_('Subject'), max_length=100)
    from_email = models.ForeignKey(
        MailFromEmail, null=True, blank=True,
        verbose_name=_('From email'), default=DEFAULT_FROM_EMAIL,
        help_text=_('If not specified, then used default.'))
    bcc_email = models.ManyToManyField(
        MailBcc, verbose_name=_('Bcc'), blank=True, null=True,
        help_text='Blind carbon copy')
    message = HTMLField(_('Body'))
    slug = models.SlugField(
        _('Slug'), unique=True,
        help_text=_('Unique slug to use in code.'))
    num_of_retries = models.PositiveIntegerField(
        _('Number of retries'), default=1)
    priority = models.SmallIntegerField(
        _('Priority'), default=DEFAULT_PRIORITY, choices=PRIORITY_STEPS)
    is_html = models.BooleanField(_('Is html'), default=True)
    is_admin = models.BooleanField(_('For admin'), default=False)
    is_active = models.BooleanField(_('Is active'), default=True)
    enable_log = models.BooleanField(_('Logging enabled'), default=True)
    category = models.ForeignKey(
        MailCategory, null=True, blank=True,
        verbose_name=_('Category'), default=DEFAULT_CATEGORY)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)
    context_note = models.TextField(
        _('Context note'), null=True, blank=True,
        help_text=_(
            'This is simple note field for context variables with description'
        )
    )

    def _clean_cache(self):
        cache.delete(self.slug, version=1)
        # cache.delete(self.slug, version=2)
        # cache.delete(self.slug, version=3)

    @classmethod
    def clean_cache(cls, **kwargs):
        for template in cls.objects.filter(**kwargs):
            template._clean_cache()

    def _clean_non_html(self):
        if not self.is_html:
            self.message = strip_tags(self.message)
            if hasattr(settings, 'MODELTRANSLATION_LANGUAGES'):
                for lang in settings.MODELTRANSLATION_LANGUAGES:
                    message = strip_tags(getattr(self, 'message_%s' % lang))
                    if message:
                        setattr(self, 'message_%s' % lang, message)

    def _premailer_transform(self):
        if self.is_html:
            self.message = premailer_transform(self.message)

    # @property
    # def bcc_list(self):
    #     return cache.get(self.slug, version=2)

    # @property
    # def files_list(self):
    #     return cache.get(self.slug, version=3)

    @classmethod
    def get_template(cls, slug):
        obj = cache.get(slug, version=1)

        if obj is not None:
            return obj

        obj = cls.objects.select_related('from_email').get(slug=slug)
        bcc_list = [o.email for o in obj.bcc_email.filter(is_active=1)]
        files_list = list(obj.files.all())
        auth_credentials = obj.from_email.get_auth()

        # For one request to cache instead of four
        obj.__dict__['bcc_list'] = bcc_list
        obj.__dict__['files_list'] = files_list
        obj.__dict__['auth_credentials'] = auth_credentials

        cache.set(slug, obj, timeout=None, version=1)

        return obj

    def save(self, *args, **kwargs):
        self._clean_cache()
        self._clean_non_html()
        self._premailer_transform()
        return super(MailTemplate, self).save(*args, **kwargs)

    def delete(self, using=None):
        self._clean_cache()
        super(MailTemplate, self).delete(using)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail template')
        verbose_name_plural = _('Mail templates')


class MailFile(models.Model):
    template = models.ForeignKey(
        MailTemplate, verbose_name=_('Template'), related_name='files')
    name = models.CharField(_('Name'), max_length=100)
    filename = models.FileField(_('File'), upload_to=_upload_mail_file)

    def _clean_cache(self):
        MailTemplate.clean_cache(pk=self.template.pk)

    def save(self, *args, **kwargs):
        self._clean_cache()
        return super(MailFile, self).save(*args, **kwargs)

    def delete(self, using=None):
        self._clean_cache()
        super(MailFile, self).delete(using)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail file')
        verbose_name_plural = _('Mail files')


class MailLogException(models.Model):
    name = models.CharField(_('Exception'), max_length=150, unique=True)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail Exception')
        verbose_name_plural = _('Mail Exception')


class MailLog(models.Model):
    is_sent = models.BooleanField(_('Is sent'), default=True, db_index=True)
    template = models.ForeignKey(MailTemplate, verbose_name=_('Template'))
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    user = models.ForeignKey(
        AUTH_USER_MODEL, verbose_name=_('User'),
        null=True, blank=True)
    error_message = models.TextField(_('Error message'), null=True, blank=True)
    error_exception = models.ForeignKey(
        MailLogException, null=True, blank=True, verbose_name=_('Exception'))
    num_of_retries = models.PositiveIntegerField(
        _('Number of retries'), default=1)

    @staticmethod
    def store_email_log(log, email_list, mail_type):
        if log and email_list:
            for email in email_list:
                MailLogEmail.objects.create(
                    log=log, email=email, mail_type=mail_type
                )

    @classmethod
    def store(cls, to, cc, bcc, is_sent, template, user, num, msg='', ex=None):
        if ex is not None:
            ex = MailLogException.objects.get_or_create(name=ex)[0]

        log = cls.objects.create(
            template=template, is_sent=is_sent, user=user,
            num_of_retries=num, error_message=msg, error_exception=ex
        )
        cls.store_email_log(log, to, 'to')
        cls.store_email_log(log, cc, 'cc')
        cls.store_email_log(log, bcc, 'bcc')

    @classmethod
    def cleanup(cls, days=7):
        date = datetime.datetime.now() - datetime.timedelta(days=days)
        cls.objects.filter(created__lte=date).delete()

    def __unicode__(self):
        return self.template.name

    class Meta:
        verbose_name = _('Mail log')
        verbose_name_plural = _('Mail logs')


class MailLogEmail(models.Model):
    log = models.ForeignKey(MailLog)
    email = models.EmailField()
    mail_type = models.CharField(_('Mail type'), choices=(
        ('cc', 'CC'),
        ('bcc', 'BCC'),
        ('to', 'TO'),
    ), max_length=3)

    def __unicode__(self):
        return self.email

    class Meta:
        verbose_name = _('Mail log email')
        verbose_name_plural = _('Mail log emails')


class MailGroup(models.Model):
    name = models.CharField(_('Group name'), max_length=100)
    slug = models.SlugField(_('Slug'), unique=True)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)

    def clean_cache(self):
        cache.delete(self.slug, version=4)

    @classmethod
    def get_emails(cls, slug):
        emails = cache.get(slug, version=4)

        if emails is not None:
            return emails

        emails = MailGroupEmail.objects.values_list(
            'email', flat=True).filter(group__slug=slug)

        cache.set(slug, emails, timeout=None, version=4)
        return emails

    def save(self, *args, **kwargs):
        self.clean_cache()
        return super(MailGroup, self).save(*args, **kwargs)

    def delete(self, using=None):
        self.clean_cache()
        super(MailGroup, self).delete(using)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail group')
        verbose_name_plural = _('Mail groups')


class MailGroupEmail(models.Model):
    name = models.CharField(_('Username'), max_length=100)
    email = models.EmailField(_('Email'))
    group = models.ForeignKey(
        MailGroup, verbose_name=_('Group'), related_name='emails')

    def save(self, *args, **kwargs):
        self.group.clean_cache()
        return super(MailGroupEmail, self).save(*args, **kwargs)

    def delete(self, using=None):
        self.group.clean_cache()
        super(MailGroupEmail, self).delete(using)

    def __unicode__(self):
        return self.email

    class Meta:
        verbose_name = _('Mail group email')
        verbose_name_plural = _('Mail group emails')
        unique_together = (('email', 'group',),)


class Signal(models.Model):
    SIGNALS = (
        'pre_save',
        'post_save',
        'pre_delete',
        'post_delete',
        'm2m_changed',
    )
    name = models.CharField(_('Name'), max_length=100)
    model = models.ForeignKey(
        'contenttypes.ContentType', verbose_name=_('Model'))
    signal = models.CharField(
        _('Signal'), choices=zip(SIGNALS, SIGNALS),
        max_length=15, default='post_save')
    template = models.ForeignKey(MailTemplate, verbose_name=_('Template'))
    group = models.ForeignKey(
        MailGroup, verbose_name=_('Email group'), blank=True, null=True,
        help_text=_('You can use group email or rules for recipients.'))
    rules = models.TextField(
        help_text=_(
            'Template should return email to send message. Example:'
            '{% if instance.is_active %}{{ instance.email }}{% endif %}.'
            'You can return a multiple emails separated by commas.'
        ), default='{{ instance.email }}', verbose_name=_('Rules'),
        null=True, blank=True
    )
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)
    is_active = models.BooleanField(_('Is active'), default=True)
    receive_once = models.BooleanField(
        _('Receive once'), default=True,
        help_text=_('Signal will be receive and send once for new db row.'))
    interval = models.PositiveIntegerField(
        _('Send interval'), default=0,
        help_text=_(
            'Specify interval to send messages after sometime. '
            'That very helpful for mailing on enterprise products.'
            'Interval must be set in the seconds.'
        ))

    def is_sent(self, pk):
        if pk is not None:
            if self.receive_once is True:
                return SignalLog.objects.filter(
                    model=self.model, model_pk=pk, signal=self).exists()
            return False

    def mark_as_sent(self, pk):
        if pk is not None:
            SignalLog.objects.create(
                model=self.model, model_pk=pk, signal=self
            )

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail signal')
        verbose_name_plural = _('Mail signals')


class SignalLog(models.Model):
    model = models.ForeignKey('contenttypes.ContentType')
    model_pk = models.BigIntegerField()
    signal = models.ForeignKey(Signal)
    created = models.DateTimeField(_('Created'), auto_now_add=True)

    def __unicode__(self):
        return self.signal.name

    class Meta:
        verbose_name = _('Signal log')
        verbose_name_plural = _('Signal logs')


class ApiKey(models.Model):
    name = models.CharField(_('Name'), max_length=25)
    api_key = models.CharField(_('Api key'), max_length=32, unique=True)
    is_active = models.BooleanField(_('Is active'), default=True)
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    updated = models.DateTimeField(_('Updated'), auto_now=True)

    def _clean_cache(self):
        cache.delete(self.api_key)

    @classmethod
    def clean_cache(cls):
        for api in cls.objects.all():
            api._clean_cache()

    def save(self, *args, **kwargs):
        self._clean_cache()
        return super(ApiKey, self).save(*args, **kwargs)

    def delete(self, using=None):
        self._clean_cache()
        super(ApiKey, self).delete(using)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _('Mail API')
        verbose_name_plural = _('Mail API')


if VERSION < (1, 7):
    initial_signals()

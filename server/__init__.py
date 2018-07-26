#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#  Copyright Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import io
import os
import zipfile

from girder import events, logger
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.item import Item
from girder.models.upload import Upload
from girder.models.user import User
from girder.utility.model_importer import ModelImporter


def _readFile(file):
    """
    Read file data into an in-memory buffer.

    :param file: File document.
    :type file: dict
    :return: A buffer that contains the file data.
    """
    buffer = io.BytesIO()
    with File().open(file) as fileHandle:
        while True:
            chunk = fileHandle.read()
            if not chunk:
                break
            buffer.write(chunk)
    return buffer


def _savePDF(event):
    """
    Extract PDF from submission ZIP file and save to a subfolder of the submission folder.

    Event info should contain the following fields:
    - submission: The submission document.
    - folder: The submission folder document.
    - file: The submission ZIP file document.
    """
    submission = event.info['submission']
    folder = event.info['folder']
    file = event.info['file']

    # Read submission ZIP file data into an in-memory buffer.
    # Reading into memory avoids managing temporary files and directories.
    zipData = _readFile(file)

    # Parse ZIP data to get PDF file name and data
    try:
        with zipfile.ZipFile(zipData) as zipFile:
            pdfItems = [
                zipItem
                for zipItem in zipFile.infolist()
                if zipItem.filename.lower().endswith('.pdf')
            ]
            if not pdfItems or len(pdfItems) > 1:
                logger.warning(
                    'Submission ZIP file contains multiple PDF files (FileId=%s)' % file['_id'])
                return
            pdfItem = pdfItems[0]
            pdfFileName = os.path.basename(pdfItem.filename)
            pdfData = zipFile.read(pdfItem)
            if not pdfData:
                logger.warning(
                    'Submission ZIP file contains empty PDF file (FileId=%s)' % file['_id'])
                return
    except zipfile.BadZipfile:
        logger.warning('Failed to process submission ZIP file (FileId=%s)' % file['_id'])
        return

    # Save PDF file to a subfolder of the submission folder
    user = User().load(submission['creatorId'], force=True)
    abstractFolder = Folder().createFolder(parent=folder, name='Abstract', creator=user)
    abstractFile = Upload().uploadFromFile(
        obj=io.BytesIO(pdfData),
        size=len(pdfData),
        name=pdfFileName,
        parentType='folder',
        parent=abstractFolder,
        user=user
    )

    # Set submission documentation URL
    submission['documentationUrl'] = \
        'https://challenge.kitware.com/api/v1/file/%s/download?contentDisposition=inline' % \
        abstractFile['_id']
    ModelImporter.model('submission', 'covalic').save(submission)


def afterPostScore(event):
    """
    Post-process submissions that were successfully scored.

    In test phases, users are required to submit an abstract in PDF format that describes
    their approach. This function extracts the PDF file from the submission ZIP file and
    saves it to a subfolder of the submission folder.

    This processing runs asynchronously to avoid delaying the scoring endpoint response.
    """
    submission = ModelImporter.model('submission', 'covalic').load(event.info['id'])
    phase = ModelImporter.model('phase', 'covalic').load(submission['phaseId'], force=True)

    # Handle only submissions to ISIC 2018 Final Test phases
    isicPhase = phase['meta'].get('isic2018', '')
    if isicPhase != 'final':
        return

    # Load submission folder
    folder = Folder().load(submission['folderId'], force=True)
    if not folder:
        return

    # Expect only one item in the folder
    items = list(Folder().childItems(folder, limit=2))
    if not items or len(items) > 1:
        return

    # Expect only one file in the item
    files = list(Item().childFiles(items[0], limit=2))
    if not files or len(files) > 1:
        return

    # Process asynchronously
    events.daemon.trigger(info={
        'submission': submission,
        'folder': folder,
        'file': files[0]
    }, callback=_savePDF)


def load(info):
    # Add event listeners
    events.bind('rest.post.covalic_submission/:id/score.after', info['name'], afterPostScore)

import { LightningElement, wire } from 'lwc';
import { CurrentPageReference } from 'lightning/navigation';
import { fireEvent } from 'c/pubsub';

export default class lwcPubsubPublisher extends LightningElement {
    @wire(CurrentPageReference) pageRef;

    handleButtonClick(event) {
        const data = {firstName: 'Maggy', lastName: 'Simpson'};
        fireEvent(this.pageRef, 'lwcButtonClick', data);
    }
}
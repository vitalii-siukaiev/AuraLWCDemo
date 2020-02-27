
import { LightningElement, wire, track } from 'lwc';
import { CurrentPageReference } from 'lightning/navigation';
import { registerListener } from 'c/pubsub';

export default class LwcPubSubSubscriber extends LightningElement {

    @wire(CurrentPageReference) pageRef;

    @track auraEventData;

    connectedCallback() {
         // subscribe to auraButtonClick event
         registerListener('auraButtonClick', this.handleAuraButtonClick, this);
    }

    handleAuraButtonClick(eventData) {
        const data = eventData;
        this.auraEventData = JSON.stringify(data);
        //data.firstName = 'Bart';
    }

}